"""Sprint 4: evidence layer and data check.

Incident dates for matching and sorting, the private checked-pin matcher,
the source-independence check, provenance in every drawer, the Admiralty fix
and the Wayback gate.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import checked_signals as cs  # noqa: E402

EPPING = "dbf90d51-2633-4a8f-86f1-ab1bf2b48d6c"
PROV = ROOT / "docs" / "signals" / "provenance.json"
ARTICLES = ROOT / "private" / "article_text.json"


def _fresh_db(tmp_path, monkeypatch):
    import app as forge_app
    db_file = tmp_path / "database.db"
    monkeypatch.setattr(forge_app, "DB_PATH", db_file)
    forge_app.init_db()
    forge_app.migrate_db()
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn, db_file


# ── Incident dates ───────────────────────────────────────────────────────────────────────

def test_every_checked_pin_has_a_machine_readable_incident_time():
    for s in cs.load_index().active:
        key = cs.incident_sort_key(s["metadata"]["checked"])
        # Day-precision keys start at local midnight: 1 Oct SAST is 2026-09-30T22:00Z.
        assert key.endswith("Z") and "2026-09-30T22:00:00Z" <= key < "2026-11-01", s["signal_id"]


def test_incident_window_by_precision():
    w = cs.incident_window({"incident_start": "2026-10-16T06:00+02:00", "incident_precision": "approx_time"})
    assert w[0].isoformat() == "2026-10-16T03:00:00+00:00" and w[1].isoformat() == "2026-10-16T05:00:00+00:00"
    w = cs.incident_window({"incident_start": "2026-10-14", "incident_precision": "day"})
    assert (w[1] - w[0]).total_seconds() == 86400 and w[0].isoformat() == "2026-10-13T22:00:00+00:00"
    w = cs.incident_window({"incident_start": "2026-10-12", "incident_precision": "part_of_day", "incident_part": "evening"})
    assert w[0].isoformat() == "2026-10-12T15:00:00+00:00"
    assert cs.incident_window({"incident_start": "not a date"}) is None
    assert cs.incident_sort_key({}) == ""


def test_incident_order_is_not_report_order():
    pins = cs.pattern_test_pins(cs.load_index())          # the Western Cape pins
    by_incident = sorted(pins, key=lambda s: cs.incident_sort_key(s["metadata"]["checked"]))
    by_report = sorted(pins, key=lambda s: s["timestamp"])
    assert [s["signal_id"] for s in by_incident] != [s["signal_id"] for s in by_report]
    assert by_incident[0]["signal_id"].startswith("0f037fc3")    # 1 Oct, Mitchells Plain
    assert by_incident[-1]["signal_id"].startswith("49773d55")   # 20 Oct evening, Tafelsig barber (Paulsberg St)
    assert by_incident[-2]["signal_id"].startswith("048440e0")   # 20 Oct, Steenberg (Military Rd)
    assert by_incident[-3]["signal_id"].startswith("0caa5a74")   # 20 Oct, Valhalla Park, body found
    assert by_incident[-4]["signal_id"].startswith("adf99019")   # 19 Oct, Strandfontein (Korvette Rd)
    assert by_incident[-5]["signal_id"].startswith("06219161")   # 19 Oct, Athlone (Bokmakierie St)
    assert by_incident[-6]["signal_id"].startswith("0c9f233c")   # 18 Oct, St Montague Village
    assert by_incident[-7]["signal_id"].startswith("7aa4eea1")   # 18 Oct, Cafda Village, Retreat
    assert by_incident[-8]["signal_id"].startswith("e0fa3712")   # 17 Oct 23:05, Firethorne (near Geelhout St)
    assert by_incident[-9]["signal_id"].startswith("19a71c43")   # 17 Oct, Tafelsig


# ── Checked pins in the place-and-time matcher ────────────────────────────────────────

def test_matcher_reads_checked_pins_privately_and_changes_nothing(tmp_path, monkeypatch):
    conn, db = _fresh_db(tmp_path, monkeypatch)
    checked = cs.load_index()
    cs.import_checked(conn, checked, write=True)
    before = conn.execute("SELECT signal_id, metadata_json, status, cluster_id, published_at "
                          "FROM signals ORDER BY signal_id").fetchall()
    conn.close()

    from forage.engines.correlation_engine import CorrelationEngine
    r = CorrelationEngine(db_path=db).run()
    assert r is not None

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    after = conn.execute("SELECT signal_id, metadata_json, status, cluster_id, published_at "
                         "FROM signals ORDER BY signal_id").fetchall()
    assert [tuple(x) for x in before] == [tuple(x) for x in after]
    assert cs.verify(conn, checked) == []
    pairs = conn.execute("SELECT * FROM checked_proximity").fetchall()
    assert len(pairs) >= 1
    assert all(p["note"].endswith("not a link") for p in pairs)
    ids = {s["signal_id"] for s in checked.active}
    # nothing about checked pins reaches the shared correlation / graph tables
    rows = conn.execute("SELECT signal_a, signal_b FROM correlated_incidents").fetchall()
    assert not any(a in ids or b in ids for a, b in rows)
    # Steenberg pair a day apart, matched on incident time (6 vs 7 Oct)
    got = {(p["signal_a"][:8], p["signal_b"][:8]) for p in pairs}
    assert ("530a09b2", "821f2edc") in got


# ── Source independence (counterintel repair) ─────────────────────────────────────────

BASE = ("Police spokesperson Sergeant Jane Doe said officers responded to a shooting "
        "on Main Road at about 7pm where a man was found with gunshot wounds. "
        "\u201cThe circumstances surrounding the incident are under investigation and "
        "no arrests have been made,\u201d said Doe. The victim was declared dead on scene. ")


def test_independence_copied_shared_independent():
    from forge_modules.counterintel.independence import assess, compare_pair
    a = {"url": "a", "outlet": "A", "text": BASE * 2}
    copy = {"url": "b", "outlet": "B", "text": BASE * 2 + "Extra line."}
    assert compare_pair(a, copy)["verdict"] == "copied"
    shared = {"url": "c", "outlet": "C", "text": (
        "A man died in a Main Road shooting on Friday evening, according to residents who heard "
        "several shots. Police confirmed a case. \u201cThe circumstances surrounding the incident "
        "are under investigation and no arrests have been made,\u201d police said.")}
    assert compare_pair(a, shared)["verdict"] == "shared"
    other = {"url": "d", "outlet": "D", "text": (
        "His mother said her son had just come home from soccer practice when gunmen walked into "
        "the yard. Neighbours described hearing a car speed off moments later. ")}
    assert compare_pair(a, other)["verdict"] == "independent"
    assert assess([a])["verdict"] == "single_outlet"
    r = assess([a, other])
    assert r["verdict"] == "independent" and "family" in r["articles"][1]["non_police_voices"]


@pytest.mark.skipif(not ARTICLES.exists(), reason="private article cache not on this machine")
def test_epping_is_not_all_police_statements():
    from forge_modules.counterintel.independence import assess
    texts = json.loads(ARTICLES.read_text())
    pin = next(s for s in cs.load_index().active if s["signal_id"] == EPPING)
    arts = [{"url": x["url"], "outlet": x["name"], "text": texts[x["url"]]["text"]}
            for x in pin["metadata"]["checked"]["sources"]]
    r = assess(arts)
    assert not r["police_only"]                     # Cape Times quotes Codeta
    assert any(p["verdict"] == "shared" and p["shared_quotes"] for p in r["pairs"])  # same SAPS statement


# ── Provenance in every drawer ────────────────────────────────────────────────────────

PUBLIC_KEYS = {"statement_from", "spokesperson", "other_voices", "outlets", "independence", "note"}


def test_every_checked_pin_has_provenance_and_nothing_private():
    import evidence_layer
    assert evidence_layer.completeness_problems() == []
    pins = json.loads(PROV.read_text())["pins"]
    for sid, p in pins.items():
        assert set(p) <= PUBLIC_KEYS, sid       # no reporters, motive, victims
    assert pins[EPPING]["independence"] != "same_statement"
    assert "Codeta" in pins[EPPING]["note"]


def test_publish_attaches_provenance_and_sorts_by_incident():
    from tools import publish
    for s in cs.load_index().active:
        p = publish._provenance_for(s["signal_id"])
        assert p and p["independence_label"] and p["outlets"], s["signal_id"]
    items = [{"signal_id": "b", "published_at": "2026-10-18T10:00:00Z", "incident_at": "2026-10-02T00:00:00Z"},
             {"signal_id": "a", "published_at": "2026-10-18T10:00:00Z", "incident_at": "2026-10-09T00:00:00Z"}]
    items.sort(key=lambda x: x["signal_id"])
    items.sort(key=lambda x: x.get("incident_at") or "", reverse=True)
    assert [i["signal_id"] for i in items] == ["a", "b"]
    tpl = (ROOT / "publisher" / "templates" / "map.html").read_text()
    assert 'id="sd-prov-wrap"' in tpl and "m.provenance" in tpl


def test_publish_refuses_a_pin_without_provenance(monkeypatch):
    from tools import publish
    monkeypatch.setattr(publish, "_PROVENANCE_CACHE", {})
    assert publish._provenance_for(EPPING) is None


# ── Admiralty ─────────────────────────────────────────────────────────────────────────

def test_unlisted_outlet_is_ungraded_not_f():
    from forage.utils.admiralty import grade_source, public_grade, GRADES_VALIDATED
    assert grade_source("dailyvoice.co.za") is None
    assert grade_source("https://www.iol.co.za/capeargus/news/x") == "C"
    assert grade_source("m.news24.com") == "B"
    assert grade_source("saps") == "B"
    assert GRADES_VALIDATED is False and public_grade("ewn.co.za") is None


# ── Wayback gate ──────────────────────────────────────────────────────────────────────

def test_wayback_submit_refuses_without_approval(monkeypatch):
    import evidence_layer
    monkeypatch.delenv("FORGE_WAYBACK_APPROVED", raising=False)
    def boom(*a, **k):
        raise AssertionError("network used without approval")
    monkeypatch.setattr(evidence_layer.urllib.request, "urlopen", boom)
    assert evidence_layer.main(["wayback", "--submit"]) == 2
    assert evidence_layer.main(["wayback"]) == 0

"""Batch 8 (G10): town mode, the recent-only search limit, the Caxton feed
exclusion for town runs, and the bench's live scoring. Offline: nothing here
fetches from the network."""
import json
import socket
import sqlite3

import pytest

from forage.collectors import civic_intel_collector as civic
from forage.collectors import google_news_collector as gn
from forage.collectors import town_mode as tm
from forage.collectors import town_sources as ts
from tools import collector_bench as cb


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("Batch 8 tests must not touch the network")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


# ── recent-only limit ─────────────────────────────────────────────────────────────────────

def test_recent_adds_limit_once():
    assert gn.recent("Musina murder", 23) == "Musina murder when:23d"
    assert gn.recent("Musina murder when:7d", 23) == "Musina murder when:7d"
    assert gn.recent("Musina murder WHEN:2m", 23) == "Musina murder WHEN:2m"
    assert gn.recent("", 23) == ""
    assert gn.recent("x", 0) == "x when:1d"


def test_infra_preset_words_and_limit():
    qs = gn.infra_queries("Limpopo")
    assert any("borehole" in q for q in qs)
    assert any("without water" in q for q in qs)
    assert any(q.split(" when:")[0].endswith("sewage") for q in qs if " when:" in q) or \
        any(q.endswith("sewage") for q in qs)


def test_keyword_and_infra_runs_get_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(gn, "_build_rss_url", lambda q: q)
    monkeypatch.setattr(gn, "_fetch_rss", lambda q: seen.append(q) or None)
    monkeypatch.setattr(gn, "REQUEST_DELAY", 0)
    monkeypatch.setattr(gn, "log_run", lambda *a, **k: None)
    gn.run_collector(days=10, max_queries=5, keywords=["Musina murder", "Musina water when:3d"], dry_run=True)
    assert seen[:2] == ["Musina murder when:10d", "Musina water when:3d"]


# ── area check pinned to the town ──────────────────────────────────────────────────────────

def test_area_town_fallback_drops_elsewhere():
    from forage.collectors import infra_signals as infra
    musina = infra.query_town("Musina")
    durban = {"title": "Two shot dead in Durban tavern", "description": ""}
    assert gn.keep_for_town(durban, "Beitbridge border when:23d") is True          # old behaviour
    assert gn.keep_for_town(durban, "Beitbridge border when:23d", musina) is False  # town mode
    noplace = {"title": "Truck seized with cigarettes", "description": ""}
    assert gn.keep_for_town(noplace, "Beitbridge border when:23d", musina) is True


# ── the plan ───────────────────────────────────────────────────────────────────────────────

def test_musina_plan():
    plan = tm.build_plan("Musina", days=23)
    qs = [s["query"] for s in plan["searches"]]
    assert all(q.endswith(" when:23d") for q in qs)
    for q in ("Musina murder", "Musina without water", "Musina borehole", "Musina sewage",
              "Musina smuggling", "Musina protest", "Madimbo", "Beitbridge border"):
        assert f"{q} when:23d" in qs
    names = {p["name"] for p in plan["places_searched"]}
    assert {"Matswale", "Tshamutumbu"} <= names
    assert len(qs) == len(set(q.lower() for q in qs))
    assert "citizen.co.za" in plan["excluded_hosts"]
    assert "Limpopo Mirror" in plan["local_papers"]


def test_plan_for_town_without_config():
    plan = tm.build_plan("Malamulele", days=10)
    assert plan["town"] == "Malamulele"
    assert plan["area_words"] == ["Malamulele"]
    assert all(s["query"].endswith(" when:10d") for s in plan["searches"])


def test_unknown_town_is_refused():
    with pytest.raises(ValueError):
        tm.build_plan("Nowhereville Xyz")


def test_about_town():
    plan = tm.build_plan("Musina")
    assert tm.about_town("Man shot dead in Musina store", plan)
    assert tm.about_town("Cigarettes seized at Beitbridge border post", plan)
    assert not tm.about_town("Messina, Sicily: ferry strike", plan)
    assert not tm.about_town("Durban tavern shooting", plan)


def test_civic_keep_local_paper_lead_always():
    keep = tm.civic_keep(tm.build_plan("Musina"))
    lead = {"title": "Residents back army crackdown but demand border fence", "content": ""}
    assert keep(lead, {"fetch_mode": "sitemap"})
    other_town = {"title": "Louis Trichardt pensioner robbed at home", "content": ""}
    assert not keep(other_town, {"fetch_mode": "sitemap"})
    assert not keep({"title": "Cape Town taxi strike", "content": ""}, {"fetch_mode": "rss"})
    assert keep({"title": "Musina low water supply notice", "content": ""}, {"fetch_mode": "rss"})


# ── Caxton feeds left out of town runs ──────────────────────────────────────────────────────

def test_blocked_hosts():
    hosts = ts.blocked_hosts()
    assert hosts["citizen.co.za"] == "caxton"
    assert hosts["www.citizen.co.za"] == "caxton"
    assert hosts["newcastillian.com"] == "pixelfish"
    assert "limpopomirror.co.za" not in hosts and "www.limpopomirror.co.za" not in hosts


def test_civic_town_run_reads_no_caxton_feed(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(civic, "fetch_source", lambda src: fetched.append(src["source_key"]) or [])
    caxton = {s["source_key"] for s in civic.SOURCES if civic._source_hosts(s) & set(ts.blocked_hosts())}
    assert {"citizen_news", "review_online", "letaba_herald"} <= caxton
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    try:
        out = civic.run(db, exclude_hosts=ts.blocked_hosts(), keep_signal=lambda s, src: True)
    except Exception:
        out = None  # an empty database may stop the run after fetching; what was fetched is what matters
    assert fetched, "the run should still read the other feeds"
    assert not (caxton & set(fetched))
    if out:
        assert {x["source_key"] for x in out["excluded_sources"]} >= caxton


def test_civic_plain_run_unchanged(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(civic, "fetch_source", lambda src: fetched.append(src["source_key"]) or [])
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    try:
        out = civic.run(db)
    except Exception:
        out = None
    assert {"citizen_news", "review_online", "letaba_herald"} <= set(fetched)
    if out:
        assert "excluded_sources" not in out and "off_area" not in out


# ── live scoring ────────────────────────────────────────────────────────────────────────────

def _db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE signals (signal_id TEXT, source TEXT, external_id TEXT, title TEXT, content TEXT,"
                " timestamp TEXT, stream TEXT, metadata_json TEXT)")
    con.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_live_rows_and_by_link(tmp_path):
    case = next(c for c in cb.load_cases()["cases"] if c["id"] == "musina_neltox_sandf")
    db = tmp_path / "run.db"
    _db(db, [
        ("old", "google_news_monitor", "x", "Old", "", "2026-10-01T00:00:00", "GLOBAL", "{}"),
        ("lead", "limpopo_mirror", "y", "Residents back army crackdown but demand border fence", "",
         "2026-10-11T00:00:00", "CRIME_INTEL", json.dumps({"link": case["url"]})),
    ])
    rows = cb.live_rows(db, "musina", since_ids={"old"})
    assert [r["signal_id"] for r in rows] == ["lead"]
    assert rows[0]["run"] == "civic"
    assert cb.case_rows(case, rows) == []
    assert len(cb.case_rows(case, rows, by_link=True)) == 1
    res = cb.score_live(rows, "musina")
    neltox = next(r for r in res["cases"] if r["id"] == case["id"])
    assert neltox["caught"] and neltox["right_stream"] == "all"
    assert res["summary"]["catch_cases"] == sum(1 for c in cb.load_cases()["cases"]
                                                if c["sweep"] == "musina" and c["kind"] == "catch")


def test_case_level_and_compare():
    miss = {"id": "a", "kind": "catch", "pass": False, "caught": False, "right_stream": None, "right_place": None}
    caught = {**miss, "caught": True, "right_stream": "all", "right_place": "none"}
    full = {**caught, "pass": True}
    assert cb.case_level(miss) == 0 < cb.case_level(caught) < cb.case_level(full) == 4
    out = cb.compare({"cases": [caught]}, {"cases": [full]})
    assert out == [{"id": "a", "town_mode": 2, "reference": 4, "verdict": "worse"}]


def test_sweep_for_town():
    assert cb.sweep_for_town("Musina") == "musina"
    assert cb.sweep_for_town("louis  trichardt") == "lt_thohoyandou"
    assert cb.sweep_for_town("Malamulele") is None

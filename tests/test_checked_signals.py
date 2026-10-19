"""Sprint 1: hand-checked pins a rebuild can't drop (tools/checked_signals.py).

Built on the standalone-signal and drawer tests: same fresh temp database,
same committed batches (24 Western Cape pins, then 4 North West pins, then 8 Eastern Cape pins,
then 1 Western Cape Sprint 5 pin: Cafda Village, Retreat, 18 Oct).
"""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SIGNALS_DIR = ROOT / "docs" / "signals"
EPPING = "dbf90d51-2633-4a8f-86f1-ab1bf2b48d6c"
WC_PINS = 25          # Western Cape: the Sprint 5 pattern-test pins (24 + Retreat, 19 Oct)
NW_PINS = 4           # North West, published 19 Oct 2026, outside the pattern test
EC_PINS = 8           # Eastern Cape, published 19 Oct 2026, outside the pattern test
ALL_PINS = WC_PINS + NW_PINS + EC_PINS


def _fresh_db(tmp_path, monkeypatch):
    import app as forge_app

    db_file = tmp_path / "database.db"
    monkeypatch.setattr(forge_app, "DB_PATH", db_file)
    forge_app.init_db()
    forge_app.migrate_db()
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn, db_file


def _copy_signals(tmp_path) -> Path:
    """A private copy of docs/signals to edit; returns its index path."""
    d = tmp_path / "signals"
    shutil.copytree(SIGNALS_DIR, d)
    return d / "index.json"


def _edit_batch(index: Path, n: int, fn):
    idx = json.loads(index.read_text())
    p = index.parent / idx["batches"][n]["file"]
    data = json.loads(p.read_text())
    fn(data)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data


def _restamp(index: Path):
    from tools.checked_signals import stamp_files
    stamp_files(index)


def _dump(conn) -> list:
    return [tuple(r) for r in conn.execute("SELECT * FROM signals ORDER BY signal_id")]


def _publish_to(tmp_path, monkeypatch, db_file, index=None):
    """Run tools/publish.py main() against a temp DB and a temp dist/."""
    import tools.publish as pub

    dist = tmp_path / "dist"
    monkeypatch.setattr(pub, "DB_PATH", db_file)
    monkeypatch.setattr(pub, "DIST", dist)
    monkeypatch.setattr(pub, "DIST_STATIC", dist / "static")
    monkeypatch.setattr(pub, "DIST_ART", dist / "articles")
    monkeypatch.setattr(pub, "DIST_CASES", dist / "cases")
    monkeypatch.setattr(pub, "DIST_ENTITIES", dist / "entities")
    if index is not None:
        monkeypatch.setattr(pub, "CHECKED_INDEX", index)
    monkeypatch.setattr(sys, "argv", ["publish.py"])
    pub.main()
    return dist


def _markers(dist: Path) -> list[dict]:
    import re
    html = (dist / "index.html").read_text()
    return json.loads(re.search(r"var RAW = (\[.*?\]);\s*$", html, re.M).group(1))


# ── The committed files ────────────────────────────────────────────────────────────

def test_index_loads_all_pins_in_order():
    from tools.checked_signals import load_index, stamp_files

    cs = load_index()
    assert [b.path.name for b in cs.batches] == [
        "western-cape-murders-2026-10-18.json",
        "western-cape-murders-2026-10-18-batch2.json",
        "north-west-killings-2026-10-19.json",
        "eastern-cape-killings-2026-10-19.json",
        "western-cape-murders-2026-10-19-sprint5.json"]
    assert [b.province for b in cs.batches] == [
        "Western Cape", "Western Cape", "North West", "Eastern Cape", "Western Cape"]
    assert len(cs.signals) == ALL_PINS and len(cs.active) == ALL_PINS and cs.retracted == {}
    assert stamp_files(check=True) == []          # every fingerprint is current
    for s in cs.signals:
        c = s["metadata"]["checked"]
        assert c["incident_precision"] in {"time", "approx_time", "part_of_day", "day"}
        assert c["reported_at"] >= c["incident_start"][:10]


# ── Done-when criteria ─────────────────────────────────────────────────────────────

def test_north_west_pins_stay_out_of_the_pattern_test():
    from tools.checked_signals import load_index, pattern_test_pins, provinces, PATTERN_TEST_PROVINCE

    cs = load_index()
    prov = provinces(cs)
    wc = pattern_test_pins(cs)
    assert PATTERN_TEST_PROVINCE == "Western Cape"
    assert len(wc) == WC_PINS and all(prov[s["signal_id"]] == "Western Cape" for s in wc)
    nw = [s for s in cs.active if prov[s["signal_id"]] == "North West"]
    assert len(nw) == NW_PINS and not ({s["signal_id"] for s in nw} & {s["signal_id"] for s in wc})
    for s in nw:
        assert -28.1 <= s["lat"] <= -24.5 and 22.5 <= s["lng"] <= 28.4   # really in North West
        assert "approx." in s["title"]
        assert not s["metadata"].get("links")


def test_north_west_batch_holds_back_lebotloane():
    data = json.loads((SIGNALS_DIR / "north-west-killings-2026-10-19.json").read_text(encoding="utf-8"))
    assert "Sprint 5" in data["scope"]
    assert all("Lebotloane" not in s["title"] for s in data["signals"])
    assert any("Lebotloane" in h["place"] for h in data["held_back"])
    doornpan = next(s for s in data["signals"] if s["title"].startswith("Doornpan"))
    area = doornpan["metadata"]["checked"]["admin_area"]
    assert area["municipality"] is None and area["district"] is None   # not stated as settled
    assert len(area["candidates"]) == 2


def test_eastern_cape_pins_stay_out_of_the_pattern_test_and_attribute_police_shootings():
    from tools.checked_signals import load_index, pattern_test_pins, provinces

    cs = load_index()
    prov = provinces(cs)
    wc_ids = {s["signal_id"] for s in pattern_test_pins(cs)}
    ec = [s for s in cs.active if prov[s["signal_id"]] == "Eastern Cape"]
    assert len(ec) == EC_PINS and not ({s["signal_id"] for s in ec} & wc_ids)
    for s in ec:
        assert -34.3 <= s["lat"] <= -30.0 and 22.5 <= s["lng"] <= 30.3   # really in the Eastern Cape
        assert "approx." in s["title"]
        assert not s["metadata"].get("links")
    police = [s for s in ec if "by police" in s["title"]]
    assert len(police) == 2
    for s in police:
        assert "IPID" in s["content"] and "No ballistic result has been reported" in s["content"]
        assert "not a finding of wrongdoing" in s["content"]
    bholotwa = next(s for s in ec if "Bholotwa" in s["title"])
    assert "cause not confirmed" in bholotwa["title"] and "not confirmed" in bholotwa["content"]
    data = json.loads((SIGNALS_DIR / "eastern-cape-killings-2026-10-19.json").read_text(encoding="utf-8"))
    assert "Sprint 5" in data["scope"]
    held = " ".join(h["place"] for h in data["held_back"])
    assert "Brickmakerskloof" in held and "Swartkops" in held
    titles = " ".join(s["title"] for s in data["signals"])
    assert "Brickmakerskloof" not in titles and "Swartkops" not in titles
    # SAPS newsroom GET ?nid= links open an arbitrary statement, so none is published.
    assert not any("saps.gov.za" in src["url"] for s in ec for src in s["metadata"]["checked"]["sources"])
    # No Eastern Cape pin cites a source that belongs to another incident.
    sebothe = next(s for s in ec if "Sebothe" in s["title"])
    assert not any("swartkops" in src["url"] for src in sebothe["metadata"]["checked"]["sources"])


def test_empty_db_one_import_builds_all_markers(tmp_path, monkeypatch):
    from tools.checked_signals import run_import

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    conn.close()
    run_import(db_file, write=True, out=lambda m: None)
    dist = _publish_to(tmp_path, monkeypatch, db_file)
    markers = _markers(dist)
    assert len(markers) == ALL_PINS
    regions = [m["region"] for m in markers]
    assert regions.count("Western Cape") == WC_PINS and regions.count("North West") == NW_PINS
    assert regions.count("Eastern Cape") == EC_PINS
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM signals WHERE source_type = 'checked' "
                        "AND status = 'reviewed' AND gravity_score IS NULL "
                        "AND processed_at IS NOT NULL").fetchone()[0] == ALL_PINS
    assert conn.execute("SELECT COUNT(*) FROM signal_actors").fetchone()[0] == 0


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    from tools.checked_signals import run_import

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    actions = run_import(db_file, write=False, out=lambda m: None)
    assert {a.kind for a in actions} == {"insert"} and len(actions) == ALL_PINS
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0


def test_import_twice_changes_nothing(tmp_path, monkeypatch):
    from tools.checked_signals import load_index, import_checked

    conn, _ = _fresh_db(tmp_path, monkeypatch)
    cs = load_index()
    import_checked(conn, cs, write=True)
    before = _dump(conn)
    second = import_checked(conn, cs, write=True)
    assert {a.kind for a in second} == {"unchanged"}
    assert _dump(conn) == before


def test_build_refused_when_a_batch_is_missing(tmp_path, monkeypatch):
    """Only batch 1 in the database: publish stops before writing dist/."""
    from tools.seed_checked_signals import load_batch, seed

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    seed(conn, load_batch(SIGNALS_DIR / "western-cape-murders-2026-10-18.json"), write=True)
    conn.close()
    with pytest.raises(SystemExit) as exc:
        _publish_to(tmp_path, monkeypatch, db_file)
    assert exc.value.code == 2
    assert not (tmp_path / "dist").exists()


def test_build_refused_when_a_pin_was_changed(tmp_path, monkeypatch):
    from tools.checked_signals import load_index, import_checked, verify

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    cs = load_index()
    import_checked(conn, cs, write=True)
    conn.execute("UPDATE signals SET lat = lat + 0.01 WHERE signal_id = ?", (EPPING,))
    conn.execute("UPDATE signals SET gravity_score = 0.9 WHERE signal_id LIKE '7bce2be1%'")
    conn.commit()
    problems = verify(conn, cs)
    assert any("different" in p and "dbf90d51" in p for p in problems)
    assert any("gravity_score" in p and "7bce2be1" in p for p in problems)
    conn.close()
    with pytest.raises(SystemExit):
        _publish_to(tmp_path, monkeypatch, db_file)


def test_import_puts_every_field_back(tmp_path, monkeypatch):
    """Engines or a decontamination run touching a pin are undone by the import."""
    from tools.checked_signals import load_index, import_checked, verify

    conn, _ = _fresh_db(tmp_path, monkeypatch)
    cs = load_index()
    import_checked(conn, cs, write=True)
    good = _dump(conn)
    conn.execute("UPDATE signals SET title = 'x', source_type = 'seed', status = 'raw', "
                 "published_at = NULL, gravity_score = 0.7, is_priority = 1, "
                 "relevance_score = 0.2, external_id = 'moved' WHERE signal_id = ?", (EPPING,))
    conn.commit()
    [a] = [a for a in import_checked(conn, cs, write=False) if a.kind != "unchanged"]
    assert a.kind == "update" and {"title", "source_type", "status", "published_at"} <= set(a.changed)
    import_checked(conn, cs, write=True)
    assert _dump(conn) == good and verify(conn, cs) == []


def test_retracted_pin_disappears_with_reason_kept(tmp_path, monkeypatch):
    import tools.publish as pub
    from tools.checked_signals import load_index, import_checked, verify, CheckedSignalError

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    import_checked(conn, load_index(), write=True)       # live state: every published pin
    index = _copy_signals(tmp_path)
    reason = "Test: police later said this was not a murder."
    _edit_batch(index, 0, lambda d: d["retractions"].append(
        {"signal_id": EPPING, "reason": reason, "retracted_at": "2026-10-20T08:00:00Z"}))
    cs = load_index(index)
    assert len(cs.active) == ALL_PINS - 1
    assert any("retracted but still published" in p for p in verify(conn, cs))
    acts = {a.signal_id: a.kind for a in import_checked(conn, cs, write=True)}
    assert acts[EPPING] == "withdraw"
    row = conn.execute("SELECT status, published_at, metadata_json FROM signals WHERE signal_id = ?",
                       (EPPING,)).fetchone()
    assert row["status"] == "dismissed" and row["published_at"] is None
    assert json.loads(row["metadata_json"])["retracted"]["reason"] == reason
    assert verify(conn, cs) == []
    assert {a.kind for a in import_checked(conn, cs, write=True)} == {"unchanged", "withdrawn"}
    assert EPPING not in {s["signal_id"] for s in pub._fetch_signals(conn)}
    conn.close()
    dist = _publish_to(tmp_path, monkeypatch, db_file, index=index)
    ids = {m["signal_id"] for m in _markers(dist)}
    assert len(ids) == ALL_PINS - 1 and EPPING not in ids
    # A retraction must name a pin in its own batch, with a reason.
    _edit_batch(index, 0, lambda d: d["retractions"].__setitem__(0, {"signal_id": EPPING, "reason": " ",
                                                                     "retracted_at": "2026-10-20"}))
    with pytest.raises(CheckedSignalError, match="reason"):
        load_index(index)


def test_after_build_check_catches_a_dropped_marker(tmp_path):
    from tools.checked_signals import load_index, count_markers_problems

    cs = load_index()
    markers = [{"signal_id": s["signal_id"]} for s in cs.active[1:]]
    html = tmp_path / "index.html"
    html.write_text("<script>\nvar RAW = " + json.dumps(markers) + ";\n</script>")
    problems = count_markers_problems(html, cs)
    assert any(f"fewer than the {ALL_PINS}" in p for p in problems)
    assert any(cs.active[0]["signal_id"][:8] in p for p in problems)


def test_publish_refuses_a_published_checked_pin_with_no_file(tmp_path, monkeypatch):
    from tools.checked_signals import load_index, import_checked, verify

    conn, _ = _fresh_db(tmp_path, monkeypatch)
    cs = load_index()
    import_checked(conn, cs, write=True)
    conn.execute("INSERT INTO signals (signal_id, source, external_id, title, content, timestamp, "
                 "source_type, published_at) VALUES ('orphan-1', 's', 'orphan-1', 'Orphan', 'c', "
                 "'2026-10-18', 'checked', '2026-10-18')")
    conn.commit()
    assert any("no checked file" in p for p in verify(conn, cs))


# ── Strict import: what gets rejected ─────────────────────────────────────────────

def _first_signal(fn):
    def edit(d):
        fn(d["signals"][0])
    return edit


@pytest.mark.parametrize("edit, match", [
    (_first_signal(lambda s: s.__setitem__("lat", -26.2)), "outside Western Cape"),
    (_first_signal(lambda s: s["metadata"]["checked"].__setitem__("sources", [])), "no source"),
    (_first_signal(lambda s: s["metadata"]["checked"].__setitem__(
        "sources", [{"name": "x", "url": "not a link"}])), "no source"),
    (_first_signal(lambda s: s["metadata"]["checked"].__setitem__("incident_start", "14 Oct")),
     "ISO 8601"),
    (_first_signal(lambda s: s["metadata"]["checked"].__setitem__("incident_start", "2026-02-30")),
     "not a real date"),
    (_first_signal(lambda s: s.__setitem__("timestamp", "yesterday")), "ISO 8601"),
    (_first_signal(lambda s: s["metadata"]["checked"].__setitem__("reported_at", "2026-10-01")),
     "before incident_start"),
    (_first_signal(lambda s: s["metadata"]["checked"].__setitem__("incident_precision", "time")),
     "UTC offset"),
    (_first_signal(lambda s: s["metadata"].pop("checked")), "metadata.checked"),
])
def test_import_rejects_bad_pins(tmp_path, edit, match):
    from tools.checked_signals import load_index, CheckedSignalError

    index = _copy_signals(tmp_path)
    _edit_batch(index, 0, edit)
    _restamp(index)
    with pytest.raises(CheckedSignalError, match=match):
        load_index(index)


def test_import_rejects_duplicate_ids_across_batches(tmp_path):
    from tools.checked_signals import load_index, CheckedSignalError

    index = _copy_signals(tmp_path)
    first = json.loads((index.parent / "western-cape-murders-2026-10-18.json").read_text())
    dup = copy.deepcopy(first["signals"][0])
    _edit_batch(index, 1, lambda d: d["signals"].append(dup))
    idx = json.loads(index.read_text())
    idx["batches"][1]["signals"] += 1
    index.write_text(json.dumps(idx))
    with pytest.raises(CheckedSignalError, match="duplicate signal_id"):
        load_index(index)


def test_import_rejects_hand_edit_without_new_fingerprint(tmp_path):
    from tools.checked_signals import load_index, CheckedSignalError

    index = _copy_signals(tmp_path)
    _edit_batch(index, 0, _first_signal(lambda s: s.__setitem__("title", "edited")))
    with pytest.raises(CheckedSignalError, match="fingerprint"):
        load_index(index)
    _restamp(index)
    assert load_index(index).signals[0]["title"] == "edited"


def test_import_rejects_truncated_or_missing_batch(tmp_path):
    from tools.checked_signals import load_index, CheckedSignalError

    index = _copy_signals(tmp_path)
    _edit_batch(index, 1, lambda d: d["signals"].pop())
    with pytest.raises(CheckedSignalError, match="index says 19"):
        load_index(index)
    (index.parent / "western-cape-murders-2026-10-18-batch2.json").unlink()
    with pytest.raises(CheckedSignalError, match="not found"):
        load_index(index)


def test_import_refuses_external_id_held_by_another_signal(tmp_path, monkeypatch):
    from tools.checked_signals import load_index, import_checked, CheckedSignalError

    conn, _ = _fresh_db(tmp_path, monkeypatch)
    cs = load_index()
    ext = cs.signals[0]["external_id"]
    conn.execute("INSERT INTO signals (signal_id, source, external_id, title, content, timestamp) "
                 "VALUES ('raw-copy', 'google_news', ?, 't', 'c', '2026-10-15')", (ext,))
    conn.commit()
    with pytest.raises(CheckedSignalError, match="raw-copy"):
        import_checked(conn, cs, write=False)
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1


# ── Wiring ─────────────────────────────────────────────────────────────────────────

def test_mega_ingest_imports_before_publishing(tmp_path, monkeypatch):
    import subprocess
    import tools.mega_ingest as mi

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(mi, "DB_PATH", db_file)
    monkeypatch.setenv("FORGE_ALLOW_PUBLISH", "1")
    calls = []

    def fake_run(cmd, **kw):
        n = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM signals WHERE source_type = 'checked'").fetchone()[0]
        calls.append((cmd, n))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mi.run_publish_deploy()["status"] == "success"
    assert len(calls) == 1 and calls[0][1] == ALL_PINS   # pins were in before publish.py ran

    import tools.checked_signals as cmod

    def broken(*a, **kw):
        raise cmod.CheckedSignalError("test")
    monkeypatch.setattr(cmod, "run_import", broken)
    assert mi.run_publish_deploy()["status"] == "refused"
    assert len(calls) == 1                            # publish.py never ran


def test_mega_ingest_publish_still_off_by_default(monkeypatch):
    import tools.mega_ingest as mi

    monkeypatch.delenv("FORGE_ALLOW_PUBLISH", raising=False)
    called = []
    monkeypatch.setattr(mi, "import_checked_signals", lambda: called.append(1) or {"status": "success"})
    assert mi.run_publish_deploy() == {"status": "skipped"}
    assert called == []


def test_local_map_checked_lens(tmp_path, monkeypatch):
    import core.db.connection as connection
    import app as forge_app
    from tools.checked_signals import load_index, import_checked

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    import_checked(conn, load_index(), write=True)
    conn.execute("INSERT INTO signals (signal_id, source, external_id, title, content, timestamp, "
                 "lat, lng, status, source_type) VALUES ('live-1', 'x', 'live-1', 'Live', 'c', "
                 "'2026-10-18', -33.9, 18.4, 'raw', 'live')")
    conn.commit()
    monkeypatch.setattr(connection, "DB_PATH", db_file)
    application = forge_app.create_app()
    application.config["TESTING"] = True
    client = application.test_client()

    def ids(lens):
        r = client.get(f"/api/signals/geojson?lens={lens}")
        assert r.status_code == 200
        return {f["properties"].get("signal_id") or f["properties"].get("id")
                for f in r.get_json()["features"]}

    checked = ids("checked")
    assert len(checked) == ALL_PINS and "live-1" not in checked
    assert ids("live") == {"live-1"}
    assert len(ids("all")) == ALL_PINS + 1
    page = client.get("/map?lens=checked")
    assert page.status_code == 200 and b"lens=checked" in page.data


def test_decontamination_keeps_checked_type(tmp_path, monkeypatch):
    from maintenance.system_decontamination import seed_source_type
    from tools.checked_signals import load_index, import_checked

    conn, _ = _fresh_db(tmp_path, monkeypatch)
    import_checked(conn, load_index(), write=True)
    seed_source_type(conn, "signals")
    assert conn.execute("SELECT COUNT(*) FROM signals WHERE source_type = 'checked'").fetchone()[0] == ALL_PINS


def test_old_seed_command_is_a_shortcut(tmp_path, monkeypatch, capsys):
    import tools.seed_checked_signals as seed_mod

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    conn.close()
    batch = SIGNALS_DIR / "western-cape-murders-2026-10-18.json"
    monkeypatch.setattr(sys, "argv", ["seed", str(batch), "--write", "--db", str(db_file)])
    assert seed_mod.main() == 0
    n = sqlite3.connect(str(db_file)).execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert n == ALL_PINS                             # one command now loads every batch
    stray = tmp_path / "stray.json"
    stray.write_text(batch.read_text())
    monkeypatch.setattr(sys, "argv", ["seed", str(stray), "--db", str(db_file)])
    assert seed_mod.main() == 2

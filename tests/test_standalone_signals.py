"""Tests for publishing hand-checked, standalone signals (18 Oct 2026).

Covers the three small changes made for the Western Cape experiment:
  - uncased auto-extracted people stay off Graph and Entities,
  - hand-check details (incident vs report date, pin note, sources) reach the map,
  - mega_ingest --dry-run no longer runs (and writes from) every collector.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_db(tmp_path, monkeypatch):
    import app as forge_app

    db_file = tmp_path / "database.db"
    monkeypatch.setattr(forge_app, "DB_PATH", db_file)
    forge_app.init_db()
    forge_app.migrate_db()
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


def _add_person(conn, name="Cyril Ramaphosa", conf=0.5):
    conn.execute(
        "INSERT INTO actors (name, type, source_type, created_at, "
        "confidence_score, automated, blacklisted) "
        "VALUES (?, 'person', 'ner', '2026-10-18T00:00:00', ?, 1, 0)",
        (name, conf),
    )
    conn.commit()


def test_uncased_auto_person_is_not_published(tmp_path, monkeypatch):
    import tools.publish as pub

    conn = _fresh_db(tmp_path, monkeypatch)
    _add_person(conn)
    monkeypatch.setattr(pub, "PUBLISHED_CASE_IDS", ())

    assert pub.PUBLISH_UNCASED_PERSONS is False
    assert pub._fetch_directory_actors(conn) == []
    assert pub._build_graph_data(conn)["nodes"] == []


def test_uncased_person_flag_still_works_when_switched_on(tmp_path, monkeypatch):
    """The old behaviour is one flag away, so the test above is meaningful."""
    import tools.publish as pub

    conn = _fresh_db(tmp_path, monkeypatch)
    _add_person(conn)
    monkeypatch.setattr(pub, "PUBLISHED_CASE_IDS", ())
    monkeypatch.setattr(pub, "PUBLISH_UNCASED_PERSONS", True)

    names = [a["name"] for a in pub._fetch_directory_actors(conn)]
    assert "Cyril Ramaphosa" in names


def test_checked_fields_reads_hand_check_and_drops_unsafe_links():
    from tools.publish import _checked_fields

    meta = json.dumps({"checked": {
        "incident": "Fri 16 Oct 2026, about 06:00",
        "reported": "17-18 Oct 2026",
        "location": "Fisher Avenue, Epping 1 (exact spot not given)",
        "sources": [
            {"name": "Cape Times (IOL)", "url": "https://iol.co.za/x"},
            {"name": "bad", "url": "javascript:alert(1)"},
            "not-a-dict",
        ],
    }})
    got = _checked_fields(meta)
    assert got["checked"] is True
    assert got["incident"] == "Fri 16 Oct 2026, about 06:00"
    assert got["reported"] == "17-18 Oct 2026"
    assert got["location_note"].startswith("Fisher Avenue")
    assert got["source_links"] == [{"name": "Cape Times (IOL)", "url": "https://iol.co.za/x"}]


def test_checked_fields_absent_or_broken_metadata_renders_as_before():
    from tools.publish import _checked_fields

    for meta in (None, "", "not json", json.dumps({"query": "x"}), json.dumps([1, 2])):
        got = _checked_fields(meta)
        assert got["checked"] is False
        assert got["source_links"] == []


def test_fetch_signals_carries_hand_check(tmp_path, monkeypatch):
    import tools.publish as pub

    conn = _fresh_db(tmp_path, monkeypatch)
    meta = {"checked": {"incident": "Fri 9 Oct 2026", "reported": "15 Oct 2026",
                        "location": "Cafda Village (suburb; approximate)",
                        "sources": [{"name": "Cape Argus (IOL)", "url": "https://iol.co.za/y"}]}}
    conn.execute(
        "INSERT INTO signals (signal_id, source, external_id, title, content, lat, lng, "
        "timestamp, status, metadata_json, is_priority, stream, relevance_score, "
        "source_type, published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s1", "Cape Argus (IOL)", "x1", "Cafda, Retreat (approx.): two shot dead", "body",
         -34.06, 18.48, "2026-10-15T10:00:00", "reviewed", json.dumps(meta), 0,
         "CRIME_INTEL", 0.0, "news", "2026-10-18T17:30:00Z"),
    )
    conn.commit()
    [sig] = pub._fetch_signals(conn)
    assert sig["checked"] is True
    assert sig["incident"] == "Fri 9 Oct 2026"
    assert sig["source_links"][0]["url"] == "https://iol.co.za/y"
    assert sig["gravity_score"] is None


def test_seed_checked_signals_round_trip(tmp_path, monkeypatch):
    """The committed Western Cape batch loads into an empty DB and publishes standalone."""
    import tools.publish as pub
    from tools.seed_checked_signals import load_batch, seed

    batch = Path(__file__).resolve().parent.parent / "docs/signals/western-cape-murders-2026-10-18.json"
    signals = load_batch(batch)
    assert len(signals) == 5
    conn = _fresh_db(tmp_path, monkeypatch)
    seed(conn, signals, write=False)
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
    seed(conn, signals, write=True)
    seed(conn, signals, write=True)  # re-run updates, never duplicates
    published = pub._fetch_signals(conn)
    assert len(published) == 5
    for s in published:
        assert s["checked"] and s["source_links"] and s["incident"] and s["reported"]
        assert s["gravity_score"] is None and s["stream"] == "CRIME_INTEL"
        assert "approx." in s["title"] and len(s["title"]) <= 80
    row = conn.execute("SELECT COUNT(*) FROM signals WHERE processed_at IS NULL").fetchone()
    assert row[0] == 0  # mega_ingest will not re-score or auto-link them
    assert conn.execute("SELECT COUNT(*) FROM signal_actors").fetchone()[0] == 0


def test_seed_rejects_signal_without_hand_check(tmp_path):
    import pytest
    from tools.seed_checked_signals import load_batch

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"signals": [{
        "signal_id": "x", "external_id": "x", "source": "s", "title": "t", "content": "c",
        "lat": -34, "lng": 18, "timestamp": "2026-10-18", "published_at": "2026-10-18",
        "metadata": {"query": "no check"}}]}))
    with pytest.raises(ValueError):
        load_batch(bad)


def test_mega_ingest_dry_run_runs_no_collector(monkeypatch):
    import tools.mega_ingest as mi

    calls = []

    class FakeCollector:
        @staticmethod
        async def async_main():
            calls.append("ran")
            return {"written": 1}

    monkeypatch.setattr(mi, "_collector_modules", {"fake": FakeCollector})
    result = asyncio.run(mi.run_all_collectors(dry_run=True))
    assert calls == []
    assert result["dry_run"] is True
    assert result["would_run"] == ["fake"]
    assert result["collectors_run"] == 0

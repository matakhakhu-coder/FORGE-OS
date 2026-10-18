"""Tests for the event drawer's data (18 Oct 2026).

The drawer on the map shows a hand-checked signal's full written story, its
dates, what the pin marks, and one tappable row per source with the website
beside the outlet's name. These tests cover the data the publisher passes
through, and that the page builds its drawer and lists safely.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
ELLIPSIS = chr(0x2026)


def _fresh_db(tmp_path, monkeypatch):
    import app as forge_app

    db_file = tmp_path / "database.db"
    monkeypatch.setattr(forge_app, "DB_PATH", db_file)
    forge_app.init_db()
    forge_app.migrate_db()
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


def _insert(conn, sid, content, meta):
    conn.execute(
        "INSERT INTO signals (signal_id, source, external_id, title, content, lat, lng, "
        "timestamp, status, metadata_json, is_priority, stream, relevance_score, "
        "source_type, published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "Test", "x-" + sid, "Title " + sid, content, -33.9, 18.5,
         "2026-10-15T10:00:00", "reviewed", json.dumps(meta) if meta else None, 0,
         "CRIME_INTEL", 0.0, "news", "2026-10-18T17:30:00Z"),
    )
    conn.commit()


def test_story_keeps_full_text_strips_html_and_caps():
    from tools.publish import STORY_MAX_CHARS, _story

    assert _story(None) == ""
    assert _story("<p>Two men <b>shot</b> dead.</p>") == "Two men shot dead."
    long = ("word " * 1000).strip()
    got = _story(long)
    assert len(got) <= STORY_MAX_CHARS + 1
    assert got.endswith(ELLIPSIS) and not got.endswith(" " + ELLIPSIS)


def test_source_domain():
    from tools.publish import _source_domain

    assert _source_domain("https://www.capetownetc.com/news/x/") == "capetownetc.com"
    assert _source_domain("https://iol.co.za/capetimes/x") == "iol.co.za"
    assert _source_domain("https://novanews.co.za/worcesterstandard/x") == "novanews.co.za"


def test_optional_drawer_fields_and_media_rules():
    from tools.publish import _checked_fields

    meta = json.dumps({"checked": {
        "sources": [{"name": "A", "url": "https://a.example/1", "date": "17 Oct 2026"}],
        "basis": "all based on police statements",
        "disagreements": ["Victims called passengers, then taxi owners", "  ", 7],
        "media": [
            {"src": "static/media/epping.jpg", "credit": "FORGE", "licence": "Own work"},
            {"src": "https://news.example/photo.jpg", "credit": "X", "licence": "Y"},
            {"src": "static/media/no-credit.jpg", "licence": "Own work"},
            {"src": "static/media/../../secret", "credit": "X", "licence": "Y"},
            "junk",
        ],
    }})
    got = _checked_fields(meta)
    assert got["source_links"][0]["date"] == "17 Oct 2026"
    assert got["basis"] == "all based on police statements"
    assert got["disagreements"] == ["Victims called passengers, then taxi owners", "7"]
    assert [m["src"] for m in got["media"]] == ["static/media/epping.jpg"]

    # Signals without the optional fields still work.
    plain = _checked_fields(json.dumps({"checked": {"incident": "x"}}))
    assert plain["basis"] is None and plain["disagreements"] == [] and plain["media"] == []
    assert _checked_fields(None)["media"] == []


def test_fetch_signals_story_only_for_hand_checked(tmp_path, monkeypatch):
    import tools.publish as pub

    conn = _fresh_db(tmp_path, monkeypatch)
    body = "First sentence. " + ("More detail here. " * 30) + "Accounts differ: the last line."
    _insert(conn, "c1", body, {"checked": {"sources": [
        {"name": "Cape Times (IOL)", "url": "https://iol.co.za/a"}]}})
    _insert(conn, "u1", body, None)
    by_id = {s["signal_id"]: s for s in pub._fetch_signals(conn)}

    checked, unchecked = by_id["c1"], by_id["u1"]
    assert checked["story"].endswith("Accounts differ: the last line.")
    assert checked["summary"].endswith("...")          # short summary unchanged
    assert checked["source_links"][0]["domain"] == "iol.co.za"
    assert unchecked["story"] is None                  # machine bodies stay short
    assert len(unchecked["summary"]) <= 223


def test_epping_batch_reaches_drawer_in_full(tmp_path, monkeypatch):
    """The live Epping pin: full 'Accounts differ' text and three distinct sites."""
    import tools.publish as pub
    from tools.seed_checked_signals import load_batch, seed

    batch = ROOT / "docs/signals/western-cape-murders-2026-10-18.json"
    conn = _fresh_db(tmp_path, monkeypatch)
    seed(conn, load_batch(batch), write=True)
    [epping] = [s for s in pub._fetch_signals(conn) if s["signal_id"].startswith("dbf90d51")]
    assert "Accounts differ" in epping["story"]
    assert "Accounts differ" not in epping["summary"]
    assert [l["domain"] for l in epping["source_links"]] == [
        "iol.co.za", "capetownetc.com", "novanews.co.za"]
    assert len({l["url"] for l in epping["source_links"]}) == 3


def test_map_template_builds_drawer_and_lists_safely():
    """Static checks on the map page: drawer present, no raw titles in HTML strings."""
    html = (ROOT / "publisher/templates/map.html").read_text(encoding="utf-8")
    assert 'id="signal-drawer"' in html
    assert "searchParams" in html and "'signal'" in html
    # Titles and source names are escaped where they go into HTML strings.
    assert re.search(r"\+\s*\(m\.title \|\| ''\)", html) is None
    assert "m.source.toUpperCase() +" not in html
    assert "(m.source || '').toUpperCase() +" not in html
    # The fixed one-second wait before opening a Wire pick is gone.
    assert "}, 1000);" not in html
    assert "zoomToShowLayer" in html

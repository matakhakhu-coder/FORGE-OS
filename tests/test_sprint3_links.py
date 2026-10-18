"""Sprint 3 (FORGE sprint plan): map lines only for real links.

A line on the map says two incidents are connected. Only an approved
incident link record draws one; shared sources and similar patterns show as
labelled text in the drawer; being in the same case draws nothing; a pattern
case shows "Pattern test" and lists its signals by incident date.

Uses the same fresh temp database and private copies of docs/signals as the
Sprint 1 tests. Every link record here is synthetic test data: the committed
batches carry none, and nothing here writes to them.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_checked_signals import (  # noqa: E402
    _copy_signals, _edit_batch, _fresh_db, _markers, _publish_to, _restamp)

LAVENDER = "821f2edc-df43-41d1-ad4a-c18b8380809e"     # batch 2
ST_MONTAGUE = "530a09b2-015a-4ac3-996e-0183e8599dfa"  # batch 2
VOORBRUG = "2a87ec3f-8763-416c-99cf-6c060faab5a1"     # batch 2, 3 Oct
N2_GATEWAY = "21b2a9b0-66c3-4045-924d-963eaa85b4a1"   # batch 2, 4 Oct
EPPING = "dbf90d51-2633-4a8f-86f1-ab1bf2b48d6c"       # batch 1
WORCESTER = "7bce2be1-912e-48e2-9fd4-c79d1611c16f"    # batch 1, 14 Oct
ZOLA = "0d2ac747-e939-48fd-a116-3d99c1a95758"         # 16 Oct, about 06:30

SRC = "https://example.org/test-source"


def _link(to, kind="incident", label="Same shooting (test)", quote="Test quote.",
          url=SRC, by="test"):
    ln = {"to": to, "type": kind, "label": label,
          "basis": {"quote": quote, "url": url},
          "approved_by": by, "approved_at": "2026-10-19"}
    return ln


def _add_links(index: Path, batch: int, sid: str, links: list[dict], restamp=True):
    def fn(data):
        for s in data["signals"]:
            if s["signal_id"] == sid:
                s["metadata"]["checked"]["links"] = links
    _edit_batch(index, batch, fn)
    if restamp:
        _restamp(index)


def _intel_links(dist: Path) -> list[dict]:
    html = (dist / "index.html").read_text()
    return json.loads(re.search(r"var INTEL_LINKS = (\[.*?\]);", html).group(1))


def _build(tmp_path, monkeypatch, index, case_pins=None, anchors=None):
    """Fresh DB, import the given batches, optionally a published case."""
    import tools.publish as pub
    from tools.checked_signals import load_index, import_checked

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    import_checked(conn, load_index(index), write=True)
    if case_pins:
        cur = conn.execute(
            "INSERT INTO cases (name, description, hypothesis, case_type, status, source_type, "
            "auto_generated, context_anchors) VALUES (?,?,?,?,?,?,0,?)",
            ("Test case", "synthetic", "synthetic", "criminal", "active", "experiment",
             json.dumps(anchors) if anchors is not None else None))
        cid = cur.lastrowid
        for sid in case_pins:
            conn.execute("INSERT INTO case_signals (case_id, signal_id, note) VALUES (?,?,?)",
                         (cid, sid, "test"))
        monkeypatch.setattr(pub, "PUBLISHED_CASE_IDS", [cid])
    conn.commit()
    conn.close()
    return _publish_to(tmp_path, monkeypatch, db_file, index=index)


# ── Done-when criteria ───────────────────────────────────────────────────────────────

def test_case_with_three_pins_and_no_link_records_draws_no_lines(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    # Three pins far apart: the old code joined every pair (3 lines).
    dist = _build(tmp_path, monkeypatch, index, case_pins=[EPPING, WORCESTER, LAVENDER])
    assert _intel_links(dist) == []
    assert len(_markers(dist)) == 24


def test_one_approved_incident_link_draws_one_labelled_line(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    _add_links(index, 1, VOORBRUG, [_link(N2_GATEWAY, label="Same suspects (test)")])
    dist = _build(tmp_path, monkeypatch, index, case_pins=[VOORBRUG, N2_GATEWAY, EPPING])
    lines = _intel_links(dist)
    assert len(lines) == 1
    assert lines[0]["label"] == "Same suspects (test)" and lines[0]["type"] == "incident"
    rel = [x for x in _markers(dist) if x.get("related")]
    assert len(rel) == 2                        # shown in both drawers
    assert all(r["related"][0]["type"] == "incident" for r in rel)


def test_shared_source_link_draws_no_line_but_shows_in_both_drawers(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    _add_links(index, 1, LAVENDER, [_link(
        ST_MONTAGUE, kind="shared_source", label="Same community source (test)",
        quote="", url=SRC)])
    dist = _build(tmp_path, monkeypatch, index, case_pins=[LAVENDER, ST_MONTAGUE])
    assert _intel_links(dist) == []
    rel = {x["title"][:12]: x["related"] for x in _markers(dist) if x.get("related")}
    assert len(rel) == 2
    for rows in rel.values():
        assert rows[0]["kind_label"] == "Shared source"
        assert rows[0]["label"] == "Same community source (test)"
        assert len(rows[0]["signal"]) == 8
    # Who approved it stays out of the public page.
    html = (dist / "index.html").read_text()
    assert "approved_by" not in html and "link_records" not in html


def test_similar_pattern_link_draws_no_line(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    _add_links(index, 0, EPPING, [_link(LAVENDER, kind="similar_pattern",
                                       label="Group shooting (test)", url="")])
    dist = _build(tmp_path, monkeypatch, index)
    assert _intel_links(dist) == []
    assert sum(1 for x in _markers(dist) if x.get("related")) == 2


def test_link_to_a_retracted_pin_draws_nothing(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    _add_links(index, 1, VOORBRUG, [_link(N2_GATEWAY)], restamp=False)

    def retract(data):
        data["retractions"] = [{"signal_id": N2_GATEWAY, "reason": "test",
                                "retracted_at": "2026-10-19"}]
    _edit_batch(index, 1, retract)
    _restamp(index)
    dist = _build(tmp_path, monkeypatch, index)
    assert _intel_links(dist) == []
    assert not any(x.get("related") for x in _markers(dist))


def test_pattern_case_shows_pattern_test_and_sorts_by_incident_date(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    pins = [WORCESTER, EPPING, VOORBRUG, N2_GATEWAY]      # 14, 16, 3, 4 Oct
    dist = _build(tmp_path, monkeypatch, index, case_pins=pins,
                  anchors={"experiment": True, "links_incidents": False})
    assert _intel_links(dist) == []
    page = next((dist / "cases").glob("*.html")).read_text()
    assert "Pattern test" in page
    assert "Evidence Confidence" not in page and "Active Investigation" not in page
    assert "Signals by incident date" in page
    order = [page.find(t) for t in ("Voorbrug", "N2 Gateway", "Worcester", "Epping")]
    assert -1 not in order and order == sorted(order)
    index_html = (dist / "index.html").read_text()
    assert re.search(r'case-entry__coe[^"]*">Pattern test<', index_html)


def test_pattern_case_keeps_time_order_within_one_day(tmp_path, monkeypatch):
    # Epping (16 Oct, 06:00) sorts after Zola Village (16 Oct, 06:30) by ID,
    # so a date-only sort put 06:30 first.
    index = _copy_signals(tmp_path)
    dist = _build(tmp_path, monkeypatch, index, case_pins=[ZOLA, EPPING],
                  anchors={"experiment": True, "links_incidents": False})
    page = next((dist / "cases").glob("*.html")).read_text()
    order = [page.find(t) for t in ("Epping", "Zola Village")]
    assert -1 not in order and order == sorted(order)


def test_ordinary_case_page_unchanged(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    dist = _build(tmp_path, monkeypatch, index, case_pins=[EPPING, WORCESTER])
    page = next((dist / "cases").glob("*.html")).read_text()
    assert "Evidence Confidence" in page and "Active Investigation" in page
    assert "Pattern test" not in page


# ── Link record validation ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("links,match", [
    ([_link(EPPING, quote="")], "needs both basis.quote and basis.url"),
    ([_link(EPPING, url="")], "needs both basis.quote and basis.url"),
    ([_link(EPPING, kind="shared_source", quote="", url="")], "quote or a link"),
    ([_link(EPPING, by="")], "approved_by"),
    ([_link(EPPING, kind="same_case")], "type must be one of"),
    ([_link(EPPING, label="")], "label is required"),
    ([_link(EPPING, url="ftp://x")], "http"),
    ([_link(LAVENDER)], "cannot link to itself"),
    ([_link("00000000-0000-4000-8000-000000000000")], "not a checked pin"),
    ([_link("dbf90d51")], "full signal_id"),
    ([dict(_link(EPPING), approved_at="soon")], "approved_at"),
    ([dict(_link(EPPING), strength=0.9)], "unknown fields"),
    ([_link(EPPING), _link(EPPING)], "already recorded"),
])
def test_bad_link_records_are_refused(tmp_path, links, match):
    from tools.checked_signals import CheckedSignalError, load_index

    index = _copy_signals(tmp_path)
    _add_links(index, 1, LAVENDER, links)
    with pytest.raises(CheckedSignalError, match=match):
        load_index(index)


def test_reverse_duplicate_link_is_refused(tmp_path):
    from tools.checked_signals import CheckedSignalError, load_index

    index = _copy_signals(tmp_path)
    _add_links(index, 1, LAVENDER, [_link(ST_MONTAGUE)], restamp=False)
    _add_links(index, 1, ST_MONTAGUE, [_link(LAVENDER)])
    with pytest.raises(CheckedSignalError, match="already recorded"):
        load_index(index)


def test_adding_a_link_without_restamp_is_a_hand_edit(tmp_path):
    from tools.checked_signals import CheckedSignalError, load_index

    index = _copy_signals(tmp_path)
    _add_links(index, 1, LAVENDER, [_link(ST_MONTAGUE)], restamp=False)
    with pytest.raises(CheckedSignalError, match="fingerprint"):
        load_index(index)


def test_committed_batches_carry_no_link_records():
    """No link goes live without the user's approval: none are committed."""
    from tools.checked_signals import load_index

    assert all("links" not in s["metadata"]["checked"] for s in load_index().signals)


def test_publisher_ignores_unapproved_link_in_a_hand_edited_db():
    import tools.publish as pub

    raw = [{"to": EPPING, "type": "incident", "label": "x",
            "basis": {"quote": "q", "url": SRC}}]                    # no approved_by
    assert pub._clean_link_records(raw) == []
    raw[0]["approved_by"] = "mk"
    raw[0]["basis"]["quote"] = ""
    assert pub._clean_link_records(raw) == []                         # incident needs a quote


# ── Template wiring ───────────────────────────────────────────────────────────────────────

def test_map_template_keeps_graph_dashes_and_labels_lines():
    t = (ROOT / "publisher" / "templates" / "map.html").read_text()
    assert "link.label ||" in t                     # line tooltip is the link label
    assert 'id="sd-rel-wrap"' in t and "m.related" in t
    assert "'line-style':        'dashed'" in t     # graph's dashed automated edges
    assert "innerHTML" not in t[t.find("var rel = document.getElementById('sd-rel')"):
                                t.find("section('sd-rel-wrap'")]

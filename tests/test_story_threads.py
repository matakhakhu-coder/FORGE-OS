"""Story threads and follow-on lines (owner policy 11 Nov 2026, docs/threads/POLICY.md).

A line stands for something a source said: each thread member needs its own
quote and link placing that event in the named conflict, with who says so.
Nothing reaches the page or records.json until an approved thread has two
approved, published members. Order is incident time, never cause; no arrow
between overlapping windows.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_checked_signals import (  # noqa: E402
    _copy_signals, _restamp)
from tests.test_sprint3_links import _add_links, _approve_test_edits, _build, _link  # noqa: E402

EPPING = "dbf90d51-2633-4a8f-86f1-ab1bf2b48d6c"       # batch 0, 16 Oct 05:00-07:00 SAST
WORCESTER = "7bce2be1-912e-48e2-9fd4-c79d1611c16f"    # batch 0, 14 Oct (day)
ZOLA = "0d2ac747-e939-48fd-a116-3d99c1a95758"         # 16 Oct, overlaps Epping
N2_GATEWAY = "21b2a9b0-66c3-4045-924d-963eaa85b4a1"   # batch 1, 4 Oct
PILOT = ROOT / "docs" / "threads" / "cape-town-taxi-route-dispute-2026.json"
SRC = "https://example.org/test-source"


def _member(sid, by="party: Test association", approved=True):
    m = {"signal_id": sid, "claim_by": by,
         "basis": {"quote": "Placed in the dispute (test).", "url": SRC, "outlet": "Test outlet"}}
    if approved:
        m.update(approved_by="test", approved_at="2026-11-11")
    return m


def _thread(members, status="approved", **kw):
    t = {"schema": "forge.thread", "version": 1, "thread_id": "test-thread",
         "title": "Test dispute", "status": status,
         "basis": [{"quote": "The dispute (test).", "url": SRC}],
         "members": members, "context": [], "excluded": [],
         "approved_by": "test", "approved_at": "2026-11-11"}
    t.update(kw)
    return t


def _write_thread(index: Path, t: dict) -> Path:
    d = index.parent.parent / "threads"
    d.mkdir(exist_ok=True)
    p = d / f"{t['thread_id']}.json"
    p.write_text(json.dumps(t, indent=2))
    return p


def _check(index: Path):
    from tools.checked_signals import load_index
    return load_index(index)


def _story_threads(dist: Path) -> list[dict]:
    html = (dist / "index.html").read_text()
    return json.loads(re.search(r"var STORY_THREADS = (\[.*?\]);\n", html).group(1))


# ── The committed pilot ─────────────────────────────────────────────────────

def test_committed_pilot_is_valid_and_private():
    from tools import threads as th
    from tools.checked_signals import load_index

    cs = load_index()                               # import runs the thread check too
    threads = th.load_threads()
    pilot = [t for t in threads if t["thread_id"] == "cape-town-taxi-route-dispute-2026"]
    assert len(pilot) == 1
    t = pilot[0]
    assert t["status"] == "approved"
    assert [m["signal_id"] for m in t["members"]] == [EPPING]
    assert t["members"][0]["claim_by"] == "party: Codeta"
    assert "Enge" in t["members"][0]["basis"]["quote"]
    # One published member: never public, so no line and nothing in records.json.
    published = [{"signal_id": s["signal_id"], "lat": s["lat"], "lng": s["lng"]} for s in cs.active]
    assert th.public_threads(threads, published) == []
    # Held candidates and the excluded events are named, never members.
    labels = " ".join(x["label"] for x in t["excluded"])
    for word in ("Maitland", "Bofors", "Bellville", "Langa Zone 5", "Delft"):
        assert word in labels
    assert all(not x.get("signal_id") for x in t["excluded"])
    # Context entries are text with a source, never pins.
    assert {c["date"] for c in t["context"]} == {"2026-10-30", "2026-11-02"}
    assert all(set(c) <= {"date", "precision", "text", "basis", "note"} for c in t["context"])


def test_policy_file_records_the_rule():
    text = (ROOT / "docs" / "threads" / "POLICY.md").read_text()
    for needle in ("11 November 2026", "map-line links", "never for something we noticed",
                   "One did not cause the next", "Held candidates stay held"):
        assert needle in text


# ── Validation ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mutate, msg", [
    (lambda t: t["members"][0].pop("claim_by"), "claim_by"),
    (lambda t: t["members"][0]["basis"].pop("url"), "quote and an http"),
    (lambda t: t["members"][0]["basis"].update(quote=""), "quote and an http"),
    (lambda t: t["members"].append(copy.deepcopy(t["members"][0])), "listed twice"),
    (lambda t: t["members"][0].update(signal_id="00000000-0000-4000-8000-000000000000"), "not an active checked pin"),
    (lambda t: t["members"][0].update(signal_id=EPPING[:8]), "full id"),
    (lambda t: t.update(status="live"), "status"),
    (lambda t: t.update(basis=[]), "basis"),
    (lambda t: t.pop("approved_by"), "approved_by"),
    (lambda t: t["context"].append({"date": "2026-11-02", "text": "x", "basis": {"quote": "q", "url": SRC},
                                    "lat": -33.9, "lng": 18.5}), "not pins"),
    (lambda t: t["context"].append({"date": "2 Nov", "text": "x", "basis": {"quote": "q", "url": SRC}}), "ISO date"),
    (lambda t: t["context"].append({"date": "2026-11-02", "text": "x"}), "basis"),
    (lambda t: t["excluded"].append({"signal_id": EPPING, "reason": "x"}), "both a member and excluded"),
    (lambda t: t.update(colour="red"), "unknown fields"),
])
def test_bad_threads_stop_the_import(tmp_path, mutate, msg):
    from tools.checked_signals import CheckedSignalError

    index = _copy_signals(tmp_path)
    t = _thread([_member(EPPING), _member(WORCESTER)])
    mutate(t)
    _write_thread(index, t)
    with pytest.raises(CheckedSignalError, match=re.escape(msg)):
        _check(index)


def test_retracted_member_stops_the_import(tmp_path):
    from tests.test_checked_signals import _edit_batch
    from tools.checked_signals import CheckedSignalError

    index = _copy_signals(tmp_path)
    _write_thread(index, _thread([_member(EPPING), _member(WORCESTER)]))
    _check(index)

    def retract(data):
        data["retractions"] = [{"signal_id": WORCESTER, "reason": "test",
                                "retracted_at": "2026-11-11"}]
    _edit_batch(index, 0, retract)
    _restamp(index)
    with pytest.raises(CheckedSignalError, match="not an active checked pin"):
        _check(index)


# ── Order and arrows ────────────────────────────────────────────────────────

def test_members_order_by_incident_time_and_overlap_has_no_arrow():
    from datetime import datetime, timezone
    from tools import threads as th

    def w(d0, h0, d1, h1):
        return (datetime(2026, 10, d0, h0, tzinfo=timezone.utc), datetime(2026, 10, d1, h1, tzinfo=timezone.utc))
    rows = [{"signal": "c", "window": w(16, 3, 16, 5)},
            {"signal": "a", "window": w(4, 3, 4, 10)},
            {"signal": "x", "window": None},
            {"signal": "b", "window": w(16, 4, 16, 6)}]
    dated, undated, segs = th.order_members(rows)
    assert [r["signal"] for r in dated] == ["a", "c", "b"]
    assert [r["signal"] for r in undated] == ["x"]           # listed, never a line end
    assert segs == [{"from": "a", "to": "c", "arrow": True},
                    {"from": "c", "to": "b", "arrow": False}]  # overlap: order unknown


def test_draft_or_unapproved_members_never_public():
    from tools import threads as th
    pub = [{"signal_id": EPPING, "lat": -33.9, "lng": 18.5, "incident_at": "2026-10-16T03:00:00Z",
            "incident_end": "2026-10-16T05:00:00Z"},
           {"signal_id": WORCESTER, "lat": -33.6, "lng": 19.4, "incident_at": "2026-10-13T22:00:00Z",
            "incident_end": "2026-10-14T22:00:00Z"}]
    assert th.public_threads([_thread([_member(EPPING), _member(WORCESTER)], status="draft")], pub) == []
    assert th.public_threads([_thread([_member(EPPING), _member(WORCESTER, approved=False)])], pub) == []
    assert th.public_threads([_thread([_member(EPPING), _member(WORCESTER)], status="retired")], pub) == []
    out = th.public_threads([_thread([_member(EPPING), _member(WORCESTER)])], pub)
    assert [m["signal"] for m in out[0]["members"]] == [WORCESTER[:8], EPPING[:8]]
    assert out[0]["segments"] == [{"from": WORCESTER[:8], "to": EPPING[:8], "arrow": True}]
    assert out[0]["caption"].endswith("One did not cause the next.")


# ── follow_on links ─────────────────────────────────────────────────────────

def _follow(to, by="police"):
    ln = _link(to, kind="follow_on", label="Follows from (test)")
    if by is not None:
        ln["claim_by"] = by
    return ln


def test_follow_on_sits_on_the_newer_pin(tmp_path):
    from tools.checked_signals import CheckedSignalError

    index = _copy_signals(tmp_path)
    _add_links(index, 0, EPPING, [_follow(WORCESTER)])      # Epping (16 Oct) names Worcester (14 Oct)
    _check(index)
    index2 = _copy_signals(tmp_path / "b")
    _add_links(index2, 0, WORCESTER, [_follow(EPPING)])
    with pytest.raises(CheckedSignalError, match="newer pin"):
        _check(index2)


def test_follow_on_refuses_overlap_and_needs_claim_by(tmp_path):
    from tools.checked_signals import CheckedSignalError

    index = _copy_signals(tmp_path)
    _add_links(index, 0, EPPING, [_follow(ZOLA)])
    with pytest.raises(CheckedSignalError, match="overlap"):
        _check(index)
    index2 = _copy_signals(tmp_path / "b")
    _add_links(index2, 0, EPPING, [_follow(WORCESTER, by=None)])
    with pytest.raises(CheckedSignalError, match="claim_by"):
        _check(index2)


# ── Publishing ──────────────────────────────────────────────────────────────

def test_committed_state_publishes_no_thread(tmp_path, monkeypatch):
    dist = _build(tmp_path, monkeypatch, _copy_signals(tmp_path))
    assert _story_threads(dist) == []
    rec = json.loads((dist / "records.json").read_text())
    assert rec["version"] == 2 and rec["threads"] == []
    assert "Not police findings" in rec["thread_caveat"]
    page = (dist / "index.html").read_text()
    assert 'id="sd-thread-wrap"' in page and 'id="tv-banner"' in page


def test_public_thread_reaches_page_and_records(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    t = _thread([_member(EPPING), _member(N2_GATEWAY, by="police")])
    t["context"] = [{"date": "2026-11-02", "text": "Week of calm (test).",
                     "basis": {"quote": "q", "url": SRC, "outlet": "Test"}}]
    _write_thread(index, t)
    dist = _build(tmp_path, monkeypatch, index)
    threads = _story_threads(dist)
    assert len(threads) == 1
    pt = threads[0]
    assert [m["signal"] for m in pt["members"]] == [N2_GATEWAY[:8], EPPING[:8]]   # 4 Oct, then 16 Oct
    assert [m["n"] for m in pt["members"]] == [1, 2]
    assert pt["segments"] == [{"from": N2_GATEWAY[:8], "to": EPPING[:8], "arrow": True}]
    assert pt["context"][0]["text"] == "Week of calm (test)."
    assert pt["members"][1]["claim_by"] == "party: Test association"
    rec = json.loads((dist / "records.json").read_text())
    assert [x["id"] for x in rec["threads"]] == ["test-thread"]
    # Nothing in a thread draws a line on the default map.
    html = (dist / "index.html").read_text()
    links = json.loads(re.search(r"var INTEL_LINKS = (\[.*?\]);", html).group(1))
    assert not any({l.get("from"), l.get("to")} == {N2_GATEWAY[:8], EPPING[:8]} for l in links)


def test_follow_on_line_points_older_to_newer(tmp_path, monkeypatch):
    index = _copy_signals(tmp_path)
    _add_links(index, 0, EPPING, [_follow(WORCESTER, by="party: Codeta")])
    assert _approve_test_edits(tmp_path, monkeypatch, index) == 1
    dist = _build(tmp_path, monkeypatch, index)
    html = (dist / "index.html").read_text()
    links = json.loads(re.search(r"var INTEL_LINKS = (\[.*?\]);", html).group(1))
    fo = [l for l in links if l["type"] == "follow_on"]
    assert len(fo) == 1
    assert (fo[0]["from"], fo[0]["to"]) == (WORCESTER[:8], EPPING[:8])
    assert fo[0]["arrow"] is True and fo[0]["claim_by"] == "party: Codeta"


# ── The page ────────────────────────────────────────────────────────────────

def test_template_thread_view_contract():
    t = (ROOT / "publisher" / "templates" / "map.html").read_text()
    for needle in (
        "var STORY_THREADS = {{ (story_threads or []) | tojson }};",
        "One did not cause the next.",
        'id="tv-exit"', 'aria-labelledby="tv-heading"', 'id="tv-status" role="status" aria-live="polite"',
        "Over time", "openAnySignal(mb.signal",              # archived members open their month
        "if (!a || !b) return;",                              # never a line with a missing end
        "if (sg.arrow) layer.addLayer(tvChevron(a, b));",     # no arrow on overlap
        "context, not a map pin",
        "html.forge-thread-view",
        "Same period as the next event: order unknown",
    ):
        assert needle in t, needle
    # The thread layer only exists inside thread view.
    assert t.count("= drawThread(") == 1
    assert "var d = drawThread(t);" in t

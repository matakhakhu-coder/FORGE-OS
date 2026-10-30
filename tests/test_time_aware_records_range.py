"""Time-aware pins, Batch 10: Records from and to dates in the page address.

A Records date range can be shared and cited as a link:
records.html?from=YYYY-MM-DD&to=YYYY-MM-DD opens filtered, changing the dates
updates the address (one Back step per edit), clearing the range clears the
address, and a bad date is ignored with a plain note. Dates are South African
calendar days, the same rule as the map (static/js/forge-pins.js). Records
keeps every record, archived months included; the canonical stays the plain
Records page, so a range link is not a second page in search.

The browser half (tests/browser/records_range_check.py) runs only with
FORGE_UI_BASE set, like the other browser checks.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "browser"))

JS = ROOT / "publisher" / "static" / "js" / "records.js"
PINS_JS = ROOT / "publisher" / "static" / "js" / "forge-pins.js"
TEMPLATE = ROOT / "publisher" / "templates" / "records.html"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")
SA = timezone(timedelta(hours=2))


def _node(script: str, payload=None) -> object:
    prog = ("const R = require(%s); const DATA = %s;\n%s"
            % (json.dumps(str(JS)), json.dumps(payload), script))
    # The program goes in on stdin: with every record inlined it is longer than one command-line argument may be.
    out = subprocess.run([NODE, "-"], input=prog, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def sa_day(value: str) -> str:
    """Python's own reading of the South African day of an incident_start."""
    v = value.strip()
    if len(v) == 10:
        return v
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SA)
    return dt.astimezone(SA).date().isoformat()


def _records() -> list[dict]:
    return json.loads((ROOT / "dist" / "records.json").read_text())["records"]


# ── Reading the address ──────────────────────────────────────────────────────────────────────

@needs_node
def test_a_link_with_a_range_gives_that_range():
    r = _node("console.log(JSON.stringify(R.parseRange(DATA)))", "?from=2026-10-05&to=2026-10-19")
    assert r == {"from": "2026-10-05", "to": "2026-10-19", "notes": []}


@needs_node
@pytest.mark.parametrize("search,frm,to", [
    ("?from=2026-10-19", "2026-10-19", ""),
    ("?to=2026-10-19", "", "2026-10-19"),
    ("", "", ""),
    ("?stream=Infrastructure", "", ""),
    ("?from=&to=", "", ""),
    ("from=2026-10-01&to=2026-10-02", "2026-10-01", "2026-10-02"),
])
def test_partial_and_empty_ranges(search, frm, to):
    r = _node("console.log(JSON.stringify(R.parseRange(DATA)))", search)
    assert (r["from"], r["to"], r["notes"]) == (frm, to, [])


@needs_node
@pytest.mark.parametrize("bad", ["2026-02-30", "2026-10", "2026-13-01", "19-10-2026", "yesterday",
                                 "2026-10-01T00:00", "1899-12-31", "2101-01-01", "<script>",
                                 "2026-1-5", "%E2%80%AE"])
def test_a_bad_date_is_ignored_with_a_note(bad):
    r = _node("console.log(JSON.stringify(R.parseRange(DATA)))", "?from=" + bad + "&to=2026-10-20")
    assert r["from"] == "" and r["to"] == "2026-10-20"
    assert len(r["notes"]) == 1 and "ignored" in r["notes"][0] and "“from”" in r["notes"][0]


@needs_node
def test_a_long_bad_date_is_clipped_in_the_note():
    r = _node("console.log(JSON.stringify(R.parseRange(DATA)))", "?to=" + "x" * 500)
    assert r["to"] == "" and len(r["notes"][0]) < 160


@needs_node
def test_dates_the_wrong_way_round_are_swapped_and_said():
    r = _node("console.log(JSON.stringify(R.parseRange(DATA)))", "?from=2026-10-20&to=2026-10-05")
    assert (r["from"], r["to"]) == ("2026-10-05", "2026-10-20")
    assert r["notes"] == ["The dates in this link were the wrong way round, so they were swapped: "
                          "5 October 2026 to 20 October 2026."]


# ── Writing the address ──────────────────────────────────────────────────────────────────────

@needs_node
@pytest.mark.parametrize("search,frm,to,want", [
    ("", "2026-10-01", "2026-10-15", "?from=2026-10-01&to=2026-10-15"),
    ("?from=2026-10-01&to=2026-10-15", "", "", ""),
    ("?from=2026-10-01&to=2026-10-15", "2026-10-03", "", "?from=2026-10-03"),
    ("?stream=Infrastructure", "", "2026-10-09", "?stream=Infrastructure&to=2026-10-09"),
    ("?stream=Infrastructure&from=2026-10-01", "", "", "?stream=Infrastructure"),
    ("?from=junk", "", "", ""),
])
def test_the_address_carries_the_range_and_keeps_the_rest(search, frm, to, want):
    got = _node("console.log(JSON.stringify(R.rangeSearch(DATA[0], DATA[1], DATA[2])))", [search, frm, to])
    assert got == want


@needs_node
def test_round_trip_address_to_range_and_back():
    got = _node("""
      const out = [];
      for (const [f, t] of DATA) {
        const s = R.rangeSearch('?stream=Crime', f, t);
        const r = R.parseRange(s);
        out.push([r.from, r.to, r.notes.length]);
      }
      console.log(JSON.stringify(out));""", [["2026-10-01", "2026-10-31"], ["", "2026-12-15"], ["2026-11-23", ""]])
    assert got == [["2026-10-01", "2026-10-31", 0], ["", "2026-12-15", 0], ["2026-11-23", "", 0]]


# ── South African days ───────────────────────────────────────────────────────────────────────

EDGES = ["2026-11-15T21:59:00Z", "2026-11-15T22:00:00Z", "2026-11-16T00:30:00+02:00",
         "2026-12-15T21:30:00Z", "2026-10-31T23:59+02:00", "2026-11-01T00:00+02:00",
         "2026-10-31T22:00Z", "2026-10-01T01:00:00+04:00", "2026-09-30T23:00:00-01:00",
         "2026-10-20T20:07", "2026-10-21", "2026-10-20T12:30:15.250+02:00"]


@needs_node
def test_records_use_the_same_south_african_day_as_the_map():
    stamps = [r["incident_start"] for r in _records()] + EDGES
    prog = ("const P = require(%s).ForgePins;" % json.dumps(str(PINS_JS)) +
            "console.log(JSON.stringify(DATA.map(s => [R.saDay(s), P.localDay(s)])))")
    got = _node(prog, stamps)
    for stamp, (ours, maps) in zip(stamps, got):
        assert ours == maps == sa_day(stamp), stamp


@needs_node
def test_south_african_midnight_decides_the_day():
    got = _node("console.log(JSON.stringify(DATA.map(s => R.matches({incident_start: s}, {from: '2026-11-16', to: '2026-11-16'}))))",
                ["2026-11-15T21:59:00Z", "2026-11-15T22:00:00Z", "2026-11-16T21:59:00Z", "2026-11-16T22:00:00Z"])
    assert got == [False, True, True, False]


@needs_node
@pytest.mark.parametrize("frm,to", [("2026-10-15", "2026-10-20"), ("2026-10-19", ""), ("", "2026-10-10"),
                                    ("2026-10-01", "2026-10-31"), ("2026-10-20", "2026-10-20"),
                                    ("2027-01-01", ""), ("", "2026-09-30")])
def test_range_filter_matches_python_on_every_record(frm, to):
    recs = _records()
    got = _node("console.log(JSON.stringify(DATA.recs.filter(r => R.matches(r, DATA.f)).map(r => r.signal_id)))",
                {"recs": recs, "f": {"from": frm, "to": to}})
    want = [r["signal_id"] for r in recs
            if (not frm or sa_day(r["incident_start"]) >= frm) and (not to or sa_day(r["incident_start"]) <= to)]
    assert got == want
    if (frm, to) == ("2026-10-01", "2026-10-31"):
        assert len(got) == len(recs) == 78


@needs_node
def test_notes_for_ranges_outside_the_records_and_backwards_typing():
    span = {"first": "2026-10-01", "last": "2026-10-23"}
    got = _node("console.log(JSON.stringify(DATA.c.map(c => R.rangeNote(c[0], c[1], DATA.span))))",
                {"span": span, "c": [["2027-01-01", ""], ["", "2026-09-30"], ["2026-10-05", "2026-10-09"],
                                     ["2026-10-09", "2026-10-05"], ["2026-09-01", "2026-12-31"], ["", ""]]})
    assert got[0] == got[1] == "No records fall in this range. Records run from 1 October 2026 to 23 October 2026."
    assert got[2] == "" and got[4] == "" and got[5] == ""
    assert "after" in got[3] and "Swap" in got[3]


# ── The page ─────────────────────────────────────────────────────────────────────────────────

def test_records_page_has_a_spoken_note_and_a_date_hint():
    t = TEMPLATE.read_text(encoding="utf-8")
    assert '<p class="rec-range-note" id="rec-range-note" role="status" hidden></p>' in t
    assert 'id="rec-dates-hint"' in t and "South African dates" in t and "page address" in t
    assert t.count('aria-describedby="rec-dates-hint"') == 2
    assert 'id="rec-count" aria-live="polite"' in t


def test_page_code_reads_writes_and_follows_back_and_forward():
    js = JS.read_text(encoding="utf-8")
    assert "parseRange(root.location.search)" in js
    assert "pushState" in js and "replaceState" in js and "'popstate'" in js
    # notes are written as text, never as markup
    assert "innerHTML" not in js
    # the steady clock: a pretend or changed date can't merge or split Back steps
    assert "performance.now" in js


def test_records_seo_is_unchanged_and_a_range_link_points_search_at_plain_records():
    page = (ROOT / "dist" / "records.html").read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://forgemap.co.za/records">' in page
    assert "noindex" not in page
    sitemap = (ROOT / "dist" / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://forgemap.co.za/records</loc>" in sitemap and "from=" not in sitemap
    t = TEMPLATE.read_text(encoding="utf-8")
    assert "{% block description %}Every FORGE Map incident in one table" in t
    assert "canonical" not in t     # the canonical comes from base.html, the plain page


# ── Built pages (the 24 October and 15 December builds) ──────────────────────────────────────

@pytest.fixture(scope="module")
def dists(tmp_path_factory):
    from time_aware_dists import build
    out = tmp_path_factory.mktemp("ta10dists")
    for name, day in (("oct", date(2026, 10, 24)), ("dec", date(2026, 12, 15))):
        dist = build(tmp_path_factory.mktemp("ta10" + name), day)
        shutil.copytree(dist, out / name)
    return out


def test_records_keep_every_record_in_both_builds(dists):
    for name in ("oct", "dec"):
        recs = json.loads((dists / name / "records.json").read_text())["records"]
        page = (dists / name / "records.html").read_text(encoding="utf-8")
        assert len(recs) == 78 and page.count('<tr data-id="') == 78, name
        assert '<link rel="canonical" href="https://forgemap.co.za/records">' in page
        assert (dists / name / "static" / "js" / "records.js").read_text() == JS.read_text()


BROWSER = os.environ.get("FORGE_UI_BASE")
needs_browser = pytest.mark.skipif(not BROWSER, reason="browser checks are off (set FORGE_UI_BASE)")


@pytest.fixture(scope="module")
def served(dists):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(dists))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield dists, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_browser
def test_browser_records_range_in_the_address(served):
    import records_range_check
    out, base = served
    rep = records_range_check.run(base, out)
    assert rep["problems"] == [], rep["problems"][:10]
    assert rep["checks"] > 100

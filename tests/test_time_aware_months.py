"""Time-aware pins, Batch 9: the Earlier months view on the map.

The map's archive is browsed one whole calendar month at a time; custom date
ranges stay in Records and Area check. An Earlier months button lists every
month from the archive's first to the current South African month, empty
ones included, with counts. A month opens at its own address, ?month=YYYY-MM,
and shows that month's pins only, read through the shared ForgePins loader.
It can never pass for the live map: a banner names the month, says "not the
live map" and carries Back to live map and the month's coverage note; the map
turns grey in a dashed frame; pins are hollow with no age ring and no pulse;
the hover words name the month. Empty months, months the archive doesn't
have, bad addresses and a failed load (Try again) are handled.

The browser half (tests/browser/earlier_months_check.py) runs only with
FORGE_UI_BASE set, like the other browser checks.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "browser"))

JS = ROOT / "publisher" / "static" / "js" / "forge-pins.js"
TEMPLATE = ROOT / "publisher" / "templates" / "map.html"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")


def _node(script: str, payload) -> object:
    prog = ("const F = require(%s).ForgePins; const DATA = %s;\n%s"
            % (json.dumps(str(JS)), json.dumps(payload), script))
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


OCT = {"month": "2026-10", "file": "2026-10.json", "count": 78, "closed": False,
       "coverage_note": "Collection started 1 October 2026 ... This is not a crime rate."}


# ── The ?month= address ───────────────────────────────────────────────────────────────────────

@needs_node
def test_month_param_accepts_real_months_only():
    vals = ["2026-10", " 2026-10 ", "2026-01", "2026-12", "2026-13", "2026-00", "2026-1",
            "oct", "", None, "2026-10-01", "<b>"]
    got = _node("console.log(JSON.stringify(DATA.map(F.monthParam)))", vals)
    assert got == ["2026-10", "2026-10", "2026-01", "2026-12", None, None, None,
                   None, None, None, None, None]


# ── The months the list offers ──────────────────────────────────────────────────────────────

def _list(doc, today):
    return _node("console.log(JSON.stringify(F.monthList(DATA.doc, DATA.today)))", {"doc": doc, "today": today})


@needs_node
def test_today_only_october_listed_as_this_month():
    rows = _list({"months": [OCT]}, "2026-10")
    assert [(r["month"], r["label"], r["count"], r["current"], r["listed"]) for r in rows] == [
        ("2026-10", "October 2026", 78, True, True)]
    assert rows[0]["coverage_note"].endswith("This is not a crime rate.")


@needs_node
def test_15_december_lists_the_empty_months_newest_first():
    rows = _list({"months": [dict(OCT, closed=True)]}, "2026-12")
    assert [(r["month"], r["count"], r["current"], r["listed"], r["file"]) for r in rows] == [
        ("2026-12", 0, True, False, None),
        ("2026-11", 0, False, False, None),
        ("2026-10", 78, False, True, "2026-10.json")]


@needs_node
def test_year_boundary_and_gaps():
    doc = {"months": [{"month": "2026-11", "count": 3}, {"month": "2027-02", "count": 5}]}
    rows = _list(doc, "2027-03")
    assert [r["month"] for r in rows] == ["2027-03", "2027-02", "2027-01", "2026-12", "2026-11"]
    assert [r["count"] for r in rows] == [0, 5, 0, 0, 3]


@needs_node
def test_empty_or_broken_index_lists_nothing_and_a_slow_clock_still_shows_every_month():
    assert _list({"months": []}, "2026-10") == []
    assert _list({}, "2026-10") == []
    assert _list(None, "2026-10") == []
    assert _list({"months": [{"month": "bad"}, None]}, "2026-10") == []
    # a visitor's clock behind the build still sees the index's months
    rows = _list({"months": [OCT]}, "2026-09")
    assert [r["month"] for r in rows] == ["2026-10"] and rows[0]["current"] is False


# ── The page ──────────────────────────────────────────────────────────────────────────────────────

def test_earlier_months_button_names_its_list():
    t = _template()
    m = re.search(r'<button[^>]*id="ctrl-months"[^>]*>', t)
    assert m
    for attr in ('aria-label="Earlier months"', 'aria-expanded="false"', 'aria-controls="mv-pop"'):
        assert attr in m.group(0)
    assert 'id="mv-pop" role="dialog"' in t
    assert "For other date ranges, use Records or Area check." in t


def test_banner_says_month_view_not_the_live_map_with_the_way_back():
    t = _template()
    banner = t[t.index('id="mv-banner"'):t.index('id="mv-status"')]
    assert "Month view &middot; not the live map" in banner
    assert ">Back to live map</button>" in banner
    assert 'id="mv-retry"' in banner and ">Try again</button>" in banner
    assert 'aria-controls="mv-note"' in banner
    assert 'id="mv-status" role="status" aria-live="polite"' in t


def test_month_view_is_marked_by_more_than_colour():
    t = _template()
    css = t[t.index("Earlier months view (time-aware pins, Batch 9)"):t.index(".mv-key-swatch {")]
    assert "border: 3px dashed" in css                       # frame round the map
    assert "filter: grayscale(1)" in css                     # grey tiles
    assert re.search(r"\.sig-ring,\s*html\.forge-month-view #map \.sig-pulse,\s*html\.forge-month-view #map \.mc-new \{ display: none; \}", css)
    assert "border: 2px dashed var(--accent-bright)" in css   # banner outline


def test_month_view_pins_never_pulse_and_never_read_as_days_ago():
    t = _template()
    assert "function pinIsNew(m) { return !monthView && !m._fromArchive && FP.isNew(m); }" in t
    title = t[t.index("function pinTitle(m) {"):t.index("function pinTitle(m) {") + 900]
    assert "month view, not the live map" in title and "'happened ' + FP.shortDate(hd)" in title
    assert "(monthView ? '+m' : '')" in t     # icons redraw on the way in and out


def test_month_view_shows_that_month_only_and_ignores_the_live_cut():
    t = _template()
    body = t[t.index("function pinShowState(ml) {"):t.index("function pinShowState(ml) {") + 900]
    month = body.index("if (monthView) {")
    assert month < body.index("ml.extra") if "ml.extra" in body else True
    assert "if (!monthView.idx[ml.idx]) return 'out';" in body
    assert "function pinsHeldBack() { return mapFiltered() || cutCount > 0 || !!monthView; }" in t


def test_month_code_reads_through_the_shared_loader_and_writes_text_only():
    t = _template()
    code = t[t.index("var mvCtrl = document.getElementById('ctrl-months');"):t.index("window.ZADMonth = {")]
    assert "FP.loadIndex()" in code and "FP.loadMonth(key)" in code and "FP.monthList(" in code
    assert "innerHTML" not in code
    assert "fetch(" not in code
    assert "history.pushState({ forgeMonth: key }" in code
    assert "window.addEventListener('popstate'" in code
    for words in ("There\\u2019s no ", "That isn\\u2019t a month the archive knows",
                  "Couldn\\u2019t load ", "Couldn\\u2019t load the earlier months.",
                  "not that nothing happened. This is not a crime rate."):
        assert words in code, words


def test_map_key_explains_the_month_view_without_adding_a_row():
    t = _template()
    assert 'id="age-key-months"' in t
    key = t[t.index('id="age-key-months"'):t.index('id="age-key-months"') + 700]
    assert "hollow, with no ring and no pulse" in key and "not the live map" in key and "Back to live map" in key


# ── The browser half ────────────────────────────────────────────────────────────────────────────

BROWSER = os.environ.get("FORGE_UI_BASE")
needs_browser = pytest.mark.skipif(not BROWSER, reason="browser checks are off (set FORGE_UI_BASE)")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from time_aware_dists import build
    out = tmp_path_factory.mktemp("ta9dists")
    for name, day in (("oct", date(2026, 10, 24)), ("dec", date(2026, 12, 15))):
        dist = build(tmp_path_factory.mktemp("ta9" + name), day)
        shutil.copytree(dist, out / name)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield out, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_browser
def test_browser_earlier_months_view(served):
    import earlier_months_check
    out, base = served
    rep = earlier_months_check.run(base, out)
    assert rep["problems"] == []
    assert all(all(r.values()) for r in rep["results"].values())


@needs_browser
@pytest.mark.parametrize("build,day", [("oct", "2026-10-24"), ("dec", "2026-12-15")])
def test_browser_every_pin_is_reachable_through_its_month_view(served, build, day):
    import nothing_missing_check
    out, base = served
    rep = nothing_missing_check.run(base + build + "/", day, ("computer", "phone"), routes=("month",), log=None)
    assert rep["problems"] == [], rep["problems"][:5]
    for size in ("computer", "phone"):
        assert rep["sizes"][size] == {"month": rep["records"]} and rep["records"] == 78

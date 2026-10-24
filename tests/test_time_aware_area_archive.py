"""Time-aware pins, Batch 6: Area check finds archived pins; the index drops its titles.

The plan: /home/sprite/forge-map-time-aware-build-batches-2026-10-24.md.

- Area check (publisher/static/js/area-check.js) loads the archive months its
  dates touch through the shared loader (ForgePins): All loads every month,
  Pick dates the months the range touches, and Last 7 or 30 days none (the
  page's live months hold them). A pin added from a month file by any route
  joins Area check's count, archived results say which month's archive they
  come from, and a failed load says so with Try again.
- The chart slider's map-follow and a heat-map cell load the months of the
  ids they name that the page does not hold, so going back in time does not
  silently drop archived pins.
- archive/index.json no longer carries a list of every pin's title. Site
  search (search-index.json) already lists every pin and opens archived
  ones, and the browser fetches the whole index before it opens any
  archived pin. The publish check refuses an index that carries one again.
- The static nothing-missing check counts Area check for archived pins.

The browser checks (tests/browser/area_archive_check.py, and the
nothing-missing check on the December build) run only when the browser
checks are switched on (FORGE_UI_BASE set), like the earlier batches'.
"""
from __future__ import annotations

import copy
import functools
import http.server
import json
import os
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

from tools import map_archive as ma  # noqa: E402
from tools import nothing_missing as nm  # noqa: E402

AREA_JS = ROOT / "publisher" / "static" / "js" / "area-check.js"
TEMPLATE = ROOT / "publisher" / "templates" / "map.html"
NODE = shutil.which("node")
ALL_PINS = 72
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")


@pytest.fixture(scope="module")
def built_dec(tmp_path_factory):
    from time_aware_dists import build
    return build(tmp_path_factory.mktemp("ta6dec"), date(2026, 12, 15))


def _area_node(script: str):
    prog = "const A = require(%s);\n%s" % (json.dumps(str(AREA_JS)), script)
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── Which months Area check loads ─────────────────────────────────────────────

@needs_node
def test_area_check_loads_months_only_for_all_and_picked_dates():
    got = _area_node("""
      const r = A.rangeFor('custom', '2026-12-15', '2026-10-05', '2026-11-02');
      const bad = A.rangeFor('custom', '2026-12-15', '2026-10-05', '');
      console.log(JSON.stringify({
        last7: A.archiveNeed('7', A.rangeFor('7', '2026-12-15')),
        last30: A.archiveNeed('30', A.rangeFor('30', '2026-12-15')),
        all: A.archiveNeed('all', A.rangeFor('all', '2026-12-15')),
        custom: A.archiveNeed('custom', r), customKey: A.archiveKey(A.archiveNeed('custom', r)),
        half: A.archiveNeed('custom', bad), allKey: A.archiveKey('all'), none: A.archiveKey(null)
      }));""")
    assert got["last7"] is None and got["last30"] is None
    assert got["all"] == "all" and got["allKey"] == "all" and got["none"] == ""
    assert got["custom"] == {"from": "2026-10-05", "to": "2026-11-02"}
    assert got["customKey"] == "2026-10..2026-11"
    assert got["half"] == "all"          # Pick dates without two days counts every date


# ── The page wiring ──────────────────────────────────────────────────────────────

def test_area_check_gets_the_loader_and_hears_about_every_added_pin():
    t = TEMPLATE.read_text(encoding="utf-8")
    for wire in ("loadArchive: loadArchiveFor", "archiveFirstDay: archiveFirstDay",
                 "archiveLabel: archiveLabel", "areaUI.addPins(added.map(areaPin))"):
        assert wire in t, wire
    # every route that adds a pin from a month file goes through one function
    assert t.count("addPin(m, idx, { fromArchive: true })") == 1
    assert "addArchivedPins([hit.pin])" in t


def test_area_check_panel_has_its_archive_line_and_try_again():
    t = TEMPLATE.read_text(encoding="utf-8")
    for el in ('id="ac-archive"', 'id="ac-archive-text"', 'id="ac-archive-retry"'):
        assert el in t, el
    js = AREA_JS.read_text(encoding="utf-8")
    assert "Couldn\\u2019t load earlier months from the archive" in js
    assert "ensureArchive();" in js and "addPins: function (list)" in js


def test_archived_pins_show_only_when_a_lookup_names_them():
    t = TEMPLATE.read_text(encoding="utf-8")
    assert "if (ml.extra && !mapFiltered()) return 'out';" in t
    assert "if (ml.extra) return 'out';" not in t


def test_slider_and_heat_map_load_the_months_of_missing_ids():
    t = TEMPLATE.read_text(encoding="utf-8")
    assert "timeIdx = idxSet(o.ids); redraw = true; fillMissing('time', o.ids);" in t
    assert "cellIdx = idxSet(o.ids); redraw = true; fillMissing('cell', o.ids);" in t
    assert "loadArchivedIds(missing)" in t


# ── The index without its title list ─────────────────────────────────────────────

def test_index_carries_no_title_list(built_dec):
    idx = json.loads((built_dec / ma.ARCHIVE_DIR / ma.INDEX_NAME).read_text(encoding="utf-8"))
    assert "search" not in idx
    assert sorted(idx) == ["built_for", "count", "live_months", "months", "pins", "schema",
                           "timezone", "window_days"]
    assert len(idx["pins"]) == ALL_PINS
    # no pin's title is anywhere in it
    text = json.dumps(idx)
    titles = [e["title"] for e in ma.all_entries(built_dec) if e.get("title")]
    assert titles and not any(t in text for t in titles)


def test_index_is_small(built_dec):
    body = (built_dec / ma.ARCHIVE_DIR / ma.INDEX_NAME).read_bytes()
    assert len(body) < 4000            # 10.5 KB with the title list (Batch 5)


def test_publish_check_refuses_a_title_list_coming_back(built_dec):
    assert ma.problems(built_dec) == []
    path = built_dec / ma.ARCHIVE_DIR / ma.INDEX_NAME
    before = path.read_text(encoding="utf-8")
    try:
        idx = json.loads(before)
        idx["search"] = [[k, "x"] for k in idx["pins"]]
        path.write_text(json.dumps(idx), encoding="utf-8")
        assert any("search list" in p for p in ma.problems(built_dec))
    finally:
        path.write_text(before, encoding="utf-8")


def test_site_search_still_reaches_every_pin(built_dec):
    docs = json.loads((built_dec / "search-index.json").read_text(encoding="utf-8"))
    docs = docs.get("docs", docs) if isinstance(docs, dict) else docs
    urls = {d.get("url") for d in docs if isinstance(d, dict)}
    for e in ma.all_entries(built_dec):
        assert f"index.html?signal={e['signal_id'][:8]}" in urls, e["signal_id"]
    assert nm.check(dist=built_dec)["routes"]["search"] == ALL_PINS


# ── Nothing missing ─────────────────────────────────────────────────────────────

def test_december_build_area_check_reaches_every_archived_pin(built_dec):
    rep = nm.check(dist=built_dec)
    assert rep["routes"]["area"] == ALL_PINS and rep["problems"] == []


def test_a_month_missing_from_the_index_is_an_area_gap(built_dec):
    data = nm.load_dist(built_dec)
    data = copy.deepcopy(data)
    del data["archive"]["months"]["2026-10"]
    rep = nm.check(data=data)
    assert rep["routes"]["area"] == 0


def test_a_wrong_archived_date_is_caught_by_area(built_dec):
    data = copy.deepcopy(nm.load_dist(built_dec))
    first = data["archive"]["months"]["2026-10"][0]
    first["incident_at"], first["incident_end"] = "2026-03-01", None
    rep = nm.check(data=data)
    mine = {p["route"] for p in rep["problems"] if p["id"] == first["signal_id"]}
    assert "area" in mine


# ── Browser ───────────────────────────────────────────────────────────────────

BROWSER = os.environ.get("FORGE_UI_BASE")
needs_browser = pytest.mark.skipif(not BROWSER, reason="browser checks are off (set FORGE_UI_BASE)")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from time_aware_dists import build
    out = tmp_path_factory.mktemp("ta6dists")
    build(out / "oct", date(2026, 10, 24))
    build(out / "dec", date(2026, 12, 15))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield out, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_browser
def test_browser_area_check_finds_archived_pins(served):
    import area_archive_check
    _, base = served
    rep = area_archive_check.run(base)
    assert rep["problems"] == []
    assert all(all(r.values()) and len(r) == 22 for r in rep["results"].values())

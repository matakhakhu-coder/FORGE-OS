"""Time-aware pins, Batch 5: the shared loader and the 30-day live cut.

The plan: docs in /home/sprite/forge-map-time-aware-build-batches-2026-10-24.md.

- publisher/static/js/forge-pins.js works out a pin's South African incident
  day exactly as tools/map_archive.py does, and a pin is live while that day
  is today or one of the 29 days before (checked here in Node against the
  Python rules, over every real pin and the edge cases).
- Its loader fetches archive/index.json and month files only when asked,
  returns each pin once, finds a pin by full or short id, and can try again
  after a failed fetch.
- The map page uses it: the live cut in pinShowState, archived pins opened
  by link, star, search, ticker and related links, and an archive note in
  the drawer.
- The static nothing-missing check follows the loader: in a December build
  every pin reaches map, link, Watchlist and search through its month file,
  (Batch 6 closed the one gap Batch 5 left, Area check.)

The browser checks (tests/browser/live_cut_check.py, and the nothing-missing
check on a December build) run only when the browser checks are switched on
(FORGE_UI_BASE set), like the earlier batches' browser checks.
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
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "browser"))

from tools import map_archive as ma  # noqa: E402
from tools import map_data  # noqa: E402
from tools import nothing_missing as nm  # noqa: E402

JS = ROOT / "publisher" / "static" / "js" / "forge-pins.js"
TEMPLATE = ROOT / "publisher" / "templates" / "map.html"
NODE = shutil.which("node")
ALL_PINS = 72
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")


def _node(script: str, payload) -> object:
    """Run script in Node with F = ForgePins and DATA = payload; print JSON."""
    prog = ("const F = require(%s).ForgePins; const DATA = %s;\n%s"
            % (json.dumps(str(JS)), json.dumps(payload), script))
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def built_dec(tmp_path_factory):
    from time_aware_dists import build
    return build(tmp_path_factory.mktemp("ta5dec"), date(2026, 12, 15))


@pytest.fixture(scope="module")
def real_pins(built_dec):
    """The 72 checked pins' map entries (their dates), from their month file."""
    return ma.all_entries(built_dec)


# ── The day rules, in Node, against tools/map_archive.py ──────────────────────────

EDGES = ["2026-10-01", "2026-10-31T23:30:00+00:00", "2026-10-31T21:59:59Z", "2026-10-31T22:00:00Z",
         "2026-10-31T23:59", "2026-10-31 12:00:00", "2026-02-30", "2026-10-01T12",
         "2026-10-01T00:30:00.123456+02:00", "2026-10-01T00:30:00-05:00", "2026-12-31T22:30:00Z",
         "garbage", "", "2026-10-01T25:00", "2026-10-01T10:00:00+0530", "2026-02-28T23:00:00Z",
         "2028-02-29", "2027-02-29"]


@needs_node
def test_local_day_matches_python_on_edge_cases():
    js = _node("console.log(JSON.stringify(DATA.map(v => F.localDay(v))))", EDGES)
    py = [str(ma.local_day(v)) if ma.local_day(v) else None for v in EDGES]
    assert js == py


@needs_node
def test_incident_day_matches_python_on_every_real_pin(real_pins):
    pins = [{k: p.get(k) for k in ("incident_start", "incident_at", "published_at")} for p in real_pins]
    assert len(pins) == ALL_PINS
    js = _node("console.log(JSON.stringify(DATA.map(p => [F.incidentDay(p), F.monthOf(p)])))", pins)
    py = [[str(ma.incident_day(p)), ma.month_of(p)] for p in pins]
    assert js == py


@needs_node
def test_the_live_window_is_today_and_the_29_days_before():
    """On 15 December (any hour, South African time) 16 November is live and
    15 November is not; the future stays live; a pin with no date stays."""
    cases = []
    for at in ("T00:00:30+02:00", "T12:00:00+02:00", "T23:59:30+02:00"):
        cases.append("2026-12-15" + at)
    pins = [{"incident_start": "2026-11-16T00:00:00+02:00"},
            {"incident_start": "2026-11-15T23:59:59+02:00"},
            {"incident_start": "2026-11-15T22:00:00Z"},     # 16 November in South Africa
            {"incident_start": "2026-12-15"},
            {"incident_start": "2026-12-20"},
            {"published_at": "2026-11-20T08:00:00Z"},
            {}]
    js = _node("""
      const out = DATA.cases.map(c => { const now = Date.parse(c);
        return { today: F.today(now), from: F.liveFrom(now),
                 live: DATA.pins.map(p => F.isLive(p, now)),
                 age: DATA.pins.map(p => F.ageDays(p, now)) }; });
      console.log(JSON.stringify(out));""", {"cases": cases, "pins": pins})
    for row in js:
        assert row["today"] == "2026-12-15" and row["from"] == "2026-11-16"
        assert row["live"] == [True, False, True, True, True, True, True]
        assert row["age"] == [29, 30, 29, 0, -5, 25, None]


@needs_node
def test_python_and_node_agree_on_who_is_live_every_day_to_march(real_pins):
    """Every real pin, every day from 1 October 2026 to 2 March 2027."""
    days, d = [], date(2026, 10, 1)
    while d <= date(2027, 3, 2):
        days.append(d.isoformat())
        d += timedelta(days=1)
    pins = [{k: p.get(k) for k in ("incident_start", "incident_at", "published_at")} for p in real_pins]
    js = _node("""console.log(JSON.stringify(DATA.days.map(d =>
        DATA.pins.map(p => F.isLive(p, Date.parse(d + 'T12:00:00+02:00')) ? 1 : 0).join(''))))""",
               {"days": days, "pins": pins})
    py = ["".join("1" if (date.fromisoformat(day) - ma.incident_day(p)).days < 30 else "0" for p in pins)
          for day in days]
    assert js == py
    assert "1" in js[0] and set(js[-1]) == {"0"}


# ── The loader, in Node, over a December build's files ─────────────────────────

LOADER_HARNESS = """
const fs = require('fs'), path = require('path');
const calls = []; let failNext = DATA.failFirst || 0;
global.fetch = (url) => { calls.push(url);
  if (failNext > 0) { failNext--; return Promise.resolve({ ok: false, status: 503 }); }
  const p = path.join(DATA.dist, url);
  if (!fs.existsSync(p)) return Promise.resolve({ ok: false, status: 404 });
  return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(fs.readFileSync(p, 'utf8'))) }); };
F.configure({ base: '' });
(async () => { const r = {};
  r.before = calls.length;
  r.knownBefore = F.knownMonth(DATA.short);
  if (DATA.failFirst) { try { await F.loadIndex(); r.firstFailed = false; } catch (e) { r.firstFailed = true; } }
  const all = await F.loadMonths('all'); r.all = all.map(p => p.signal_id);
  const again = await F.loadMonths({ from: '2026-10-05', to: '2026-12-31' }); r.again = again.length;
  r.none = (await F.loadMonths({ from: '2027-01', to: '2027-03' })).length;
  r.list = (await F.loadMonths(['2026-10', '2026-10'])).length;
  const hit = await F.findPin(DATA.short); r.short = hit && [hit.pin.signal_id, hit.month];
  const full = await F.findPin(DATA.full.toUpperCase()); r.full = full && full.pin.signal_id;
  r.missing = await F.findPin('deadbeef');
  r.bad = await F.findPin('<script>');
  r.knownAfter = [F.knownMonth(DATA.short), F.knownMonth('deadbeef')];
  r.dedupe = F.dedupe(all, all.slice(0, 5)).length;
  r.calls = calls;
  console.log(JSON.stringify(r)); })().catch(e => { console.error(e); process.exit(1); });
"""


@needs_node
def test_loader_reads_months_once_and_finds_pins(built_dec):
    entries = ma.all_entries(built_dec)
    assert len(entries) == ALL_PINS
    first = entries[0]["signal_id"]
    r = _node(LOADER_HARNESS, {"dist": str(built_dec), "short": first[:8], "full": first})
    assert r["before"] == 0 and r["knownBefore"] is None
    assert sorted(r["all"]) == sorted(e["signal_id"] for e in entries) and len(set(r["all"])) == ALL_PINS
    assert r["again"] == ALL_PINS and r["none"] == 0 and r["list"] == ALL_PINS
    assert r["short"] == [first, "2026-10"] and r["full"] == first
    assert r["missing"] is None and r["bad"] is None
    assert r["knownAfter"] == ["2026-10", ""]
    assert r["dedupe"] == ALL_PINS
    # One index fetch and one month fetch for everything above.
    index = json.loads((built_dec / "archive" / "index.json").read_text())
    assert r["calls"] == ["archive/index.json", index["months"][0]["file"]]


@needs_node
def test_loader_tries_again_after_a_failed_fetch(built_dec):
    first = ma.all_entries(built_dec)[0]["signal_id"]
    r = _node(LOADER_HARNESS, {"dist": str(built_dec), "short": first[:8], "full": first, "failFirst": 1})
    assert r["firstFailed"] is True
    assert len(r["all"]) == ALL_PINS
    assert r["calls"][:2] == ["archive/index.json", "archive/index.json"]


# ── The map page is wired to it ────────────────────────────────────────────────

def test_template_loads_forge_pins_before_the_map_code():
    html = TEMPLATE.read_text(encoding="utf-8")
    i = html.index('src="static/js/forge-pins.js"')
    assert i < html.index('src="static/js/area-check.js"')
    assert i < html.index("var RAW = ")


def test_template_draws_only_live_pins_by_default():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "m._live = computeLive(m);" in html
    assert "if (m._live && !opts.fromArchive) clusterGroup.addLayer(marker);" in html
    # archived pins stay off unless a lookup names them (Batch 6)
    assert "if (ml.extra && !mapFiltered()) return 'out';" in html
    assert "(timeIdx || cellIdx || (RAW[ml.idx] && RAW[ml.idx]._live)) ? 'in' : 'out'" in html
    assert "if (FP.today() !== liveDay) applyLiveCut();" in html


def test_every_way_in_goes_through_the_loader():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "return openAnySignal(sid, { trigger: trigger || null });" in html    # search, Watchlist, charts
    assert "openAnySignal(a.getAttribute('data-signal')" in html                  # ticker and Wire links
    assert "openAnySignal(r.signal, { trigger: a })" in html                       # related pins
    assert "pinFromArchive(param).then" in html                                    # ?signal= links
    assert 'id="sd-archive"' in html and "' archive. Older than 30 days" in html


def test_no_page_text_promises_a_crime_rate_or_a_months_view():
    """Batch 5 adds no visible months view (that is Batch 9) and no new copy
    beyond the archive note and the load message."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "Earlier months" not in html


# ── The static nothing-missing check follows the loader ────────────────────────

def test_december_build_reaches_every_pin_every_way(built_dec):
    # Batch 5 left Area check as the one gap; Batch 6 closes it (Area check
    # loads the months its dates touch). tests/test_time_aware_area_archive.py
    data = nm.load_dist(built_dec)
    assert data["markers"] == [] and data["archive"]["live_months"] == ["2026-11", "2026-12"]
    rep = nm.check(data=data)
    assert rep["routes"] == {r: ALL_PINS for r in nm.ROUTES}
    assert rep["problems"] == []


def test_an_archived_pin_missing_from_its_month_is_caught(built_dec):
    data = nm.load_dist(built_dec)
    gone = data["archive"]["months"]["2026-10"].pop(0)
    rep = nm.check(data=data)
    mine = {p["route"] for p in rep["problems"] if p["id"] == gone["signal_id"]}
    assert "map" in mine and "link" in mine


def test_a_wrong_lookup_breaks_the_link(built_dec):
    data = nm.load_dist(built_dec)
    first = data["archive"]["months"]["2026-10"][0]
    data["archive"]["lookup"][first["signal_id"][:8]] = "2026-09"
    rep = nm.check(data=data)
    mine = {p["route"] for p in rep["problems"] if p["id"] == first["signal_id"]}
    assert "map" in mine and "link" in mine


def test_a_pin_missing_from_a_live_month_is_still_a_gap(built_dec):
    """If the index says a pin's month is live, the page must carry it."""
    data = nm.load_dist(built_dec)
    data["archive"]["live_months"].append("2026-10")
    rep = nm.check(data=data)
    assert rep["routes"]["map"] == 0
    assert all("live list" in p["why"] for p in rep["problems"] if p["route"] == "map")


def test_archived_details_files_are_checked(built_dec):
    first = ma.all_entries(built_dec)[0]["signal_id"]
    path = built_dec / map_data.PIN_DIR / f"{first[:8]}.json"
    saved = path.read_text(encoding="utf-8")
    try:
        path.write_text("{}", encoding="utf-8")
        rep = nm.check(data=nm.load_dist(built_dec))
        assert any(p["id"] == first and p["route"] == "map" for p in rep["problems"])
    finally:
        path.write_text(saved, encoding="utf-8")


# ── Browser checks (switched on with FORGE_UI_BASE) ────────────────────────────

BROWSER = os.environ.get("FORGE_UI_BASE")
needs_browser = pytest.mark.skipif(not BROWSER, reason="browser checks are off (set FORGE_UI_BASE)")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from time_aware_dists import build_all
    out = tmp_path_factory.mktemp("ta5dists")
    build_all(out)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield out, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_browser
def test_browser_live_cut(served):
    import live_cut_check
    out, base = served
    rep = live_cut_check.run(base + "synth/", out / "synth")
    assert rep["problems"] == []
    assert rep["counts"] == {"live_15_dec": 63, "held": 31, "all": 166, "live_16_dec": 59}


@needs_browser
def test_browser_december_build_reaches_archived_pins(served):
    import nothing_missing_check
    out, base = served
    rep = nothing_missing_check.run(base + "dec/", "2026-12-15", ("computer", "phone"))
    assert rep["problems"] == []
    for size in ("computer", "phone"):       # Area check too, from Batch 6
        assert rep["sizes"][size] == {r: ALL_PINS for r in nm.ROUTES}

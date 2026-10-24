"""Time-aware pins, Batch 1: the safety net before anything changes.

  - nothing-missing check (tools/nothing_missing.py): every approved record in
    records.json is reachable by the map, a ?signal= link, search, the
    Watchlist and Area check, and the check really fails when one is not
  - the clock helper (tests/browser/forge_clock.py) that lets browser tests
    pretend today is 31 October or 15 December
  - the load test's made-up pins (tools/map_load_test.py) and the saved
    baseline numbers

The browser versions run when FORGE_UI_BASE points at a served dist/:
    python -m http.server -d dist 8765 &
    FORGE_UI_BASE=http://127.0.0.1:8765/ pytest tests/test_time_aware_baseline.py
"""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "browser"))

import forge_clock  # noqa: E402
import map_load_test as lt  # noqa: E402
import nothing_missing as nm  # noqa: E402

DIST = ROOT / "dist"
BASELINE = ROOT / "docs" / "perf" / "map-load-baseline-2026-10-24.json"
UI_BASE = os.environ.get("FORGE_UI_BASE")


def _have_dist() -> bool:
    return all((DIST / f).exists() for f in ("index.html", "records.json", "search-index.json"))


@pytest.fixture(scope="module")
def data():
    if not _have_dist():
        pytest.skip("dist not built")
    return nm.load_dist(DIST)


def _routes(rep, route):
    return {p["short"] for p in rep["problems"] if p["route"] == route}


# ── Nothing missing, on today's build ───────────────────────────────────────────────

def test_every_approved_record_is_reachable_every_way(data):
    rep = nm.check(data=data)
    assert rep["records"] > 0
    assert rep["problems"] == [], rep["problems"][:10]
    for route in nm.ROUTES:
        assert rep["routes"][route] == rep["records"], route


def test_cli_passes_on_todays_build(data, capsys):
    assert nm.main(["--dist", str(DIST)]) == 0
    assert "PASS" in capsys.readouterr().out


# ── … and it fails when something goes missing ─────────────────────────────────────

def _marker(d, rec):
    return next(m for m in d["markers"] if m["signal_id"] == rec["signal_id"])


def test_fails_when_a_pin_leaves_the_markers(data):
    d = copy.deepcopy(data)
    rec = d["records"][0]
    d["markers"] = [m for m in d["markers"] if m["signal_id"] != rec["signal_id"]]
    rep = nm.check(data=d)
    short = rec["signal_id"][:8]
    for route in ("map", "link", "watchlist", "area"):
        assert short in _routes(rep, route), route


def test_fails_when_a_pin_loses_its_position(data):
    d = copy.deepcopy(data)
    rec = d["records"][1]
    _marker(d, rec)["lat"] = None
    rep = nm.check(data=d)
    assert rec["signal_id"][:8] in _routes(rep, "map")
    assert rec["signal_id"][:8] in _routes(rep, "area")


def test_fails_when_search_loses_a_pin(data):
    d = copy.deepcopy(data)
    rec = d["records"][2]
    short = rec["signal_id"][:8]
    d["search"] = [x for x in d["search"] if x.get("signal") != short]
    assert short in _routes(nm.check(data=d), "search")


def test_fails_when_a_search_result_points_elsewhere(data):
    d = copy.deepcopy(data)
    rec = d["records"][2]
    short = rec["signal_id"][:8]
    for x in d["search"]:
        if x.get("signal") == short:
            x["url"] = "records.html"
    assert short in _routes(nm.check(data=d), "search")


def test_fails_when_two_pins_share_a_short_id(data):
    d = copy.deepcopy(data)
    a, b = d["records"][3], d["records"][4]
    impostor = copy.deepcopy(_marker(d, b))
    impostor["signal_id"] = a["signal_id"][:8] + "-ffff-4fff-8fff-ffffffffffff"
    d["markers"].insert(0, impostor)          # idxForSignal takes the first match
    rep = nm.check(data=d)
    assert a["signal_id"][:8] in _routes(rep, "link")


def test_fails_when_area_check_dates_a_pin_on_another_day(data):
    d = copy.deepcopy(data)
    rec = d["records"][5]
    _marker(d, rec)["incident_at"] = "2001-01-01T10:00:00+02:00"
    _marker(d, rec)["incident_end"] = None
    assert rec["signal_id"][:8] in _routes(nm.check(data=d), "area")


def test_fails_when_area_check_cannot_read_the_date(data):
    d = copy.deepcopy(data)
    rec = d["records"][6]
    _marker(d, rec)["incident_at"] = ""
    assert rec["signal_id"][:8] in _routes(nm.check(data=d), "area")


def test_fails_when_records_json_lists_a_pin_twice(data):
    d = copy.deepcopy(data)
    d["records"].append(copy.deepcopy(d["records"][0]))
    assert d["records"][0]["signal_id"][:8] in _routes(nm.check(data=d), "map")


# ── The date rules the check copies from area-check.js ────────────────────────────

def test_area_days_follows_pinDays():
    # 22:00 UTC is already the next day in South Africa
    assert nm.area_days({"incident_at": "2026-10-20T22:00:00Z"}) == {"start": "2026-10-21", "end": "2026-10-21"}
    # an end at midnight belongs to the day before
    assert nm.area_days({"incident_at": "2026-10-05T00:00:00+02:00",
                         "incident_end": "2026-10-07T00:00:00+02:00"}) == {"start": "2026-10-05", "end": "2026-10-06"}
    # an end before the start is ignored
    assert nm.area_days({"incident_at": "2026-10-05T10:00:00+02:00",
                         "incident_end": "2026-10-01T10:00:00+02:00"}) == {"start": "2026-10-05", "end": "2026-10-05"}
    assert nm.area_days({"incident_at": None}) is None
    assert nm.area_days({"incident_at": "not a date"}) is None


def test_record_incident_day_reads_both_shapes():
    assert nm.record_incident_day({"incident_start": "2026-10-21"}) == "2026-10-21"
    assert nm.record_incident_day({"incident_start": "2026-10-16T23:30:00Z"}) == "2026-10-17"
    assert nm.record_incident_day({"incident_start": None}) is None


# ── Clock helper ────────────────────────────────────────────────────────────────

def test_clock_days_are_south_african_time():
    assert forge_clock.sa_instant("2026-10-31").astimezone(timezone.utc) == \
        datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
    assert forge_clock.epoch_ms("2026-12-15", "00:00") == \
        int(datetime(2026, 12, 14, 22, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert forge_clock.epoch_ms("2026-10-31", "23:59:30") == \
        int(datetime(2026, 10, 31, 21, 59, 30, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.mark.parametrize("day,at", [("31 Oct", "12:00"), ("2026-02-30", "12:00"), ("2026-10-31", "25:00"),
                                    ("2026-10-31", "noon"), (None, "12:00")])
def test_clock_rejects_bad_input(day, at):
    with pytest.raises(ValueError):
        forge_clock.sa_instant(day, at)


def test_freeze_today_sets_the_playwright_clock():
    calls = []

    class Clock:
        def set_fixed_time(self, when):
            calls.append(when)

    class Target:
        clock = Clock()

    ms = forge_clock.freeze_today(Target(), "2026-10-31")
    assert calls == [forge_clock.sa_instant("2026-10-31")]
    assert ms == forge_clock.epoch_ms("2026-10-31")
    with pytest.raises(RuntimeError):
        forge_clock.freeze_today(object(), "2026-10-31")


# ── Load test: made-up pins and the saved baseline ─────────────────────────────────

@pytest.fixture(scope="module")
def index_html():
    if not _have_dist():
        pytest.skip("dist not built")
    return (DIST / "index.html").read_text(encoding="utf-8")


def test_made_up_pins_look_like_real_ones(index_html):
    real = lt.load_raw(index_html)
    fake = lt.synthetic_markers(real, 1000)
    assert len(fake) == 1000
    assert len({m["signal_id"] for m in fake}) == 1000
    assert len({m["signal_id"][:8] for m in fake}) == 1000          # ?signal= stays unambiguous
    assert not {m["signal_id"] for m in fake} & {m["signal_id"] for m in real}
    keys = set(real[0])
    assert all(set(m) == keys for m in fake)
    for m in fake:
        assert lt.ZA_BOX["lat"][0] <= m["lat"] <= lt.ZA_BOX["lat"][1]
        assert lt.ZA_BOX["lng"][0] <= m["lng"] <= lt.ZA_BOX["lng"][1]
    assert lt.synthetic_markers(real, 1000) == fake                 # same every run


def test_made_up_page_swaps_markers_and_chart_rows_only(index_html):
    page = lt.synthetic_page(index_html, 500)
    assert len(lt.load_raw(page)) == 500
    chart = json.loads(lt.CHART_RE.search(page).group(2))
    assert len(chart["rows"]) == 500 and chart["total"] == 500
    # outside the two data blocks the page is byte-for-byte today's
    strip = lambda h: lt.CHART_RE.sub("", lt.RAW_RE.sub("", h))  # noqa: E731
    assert strip(page) == strip(index_html)
    assert lt.page_sizes(page)["html_bytes"] > lt.page_sizes(index_html)["html_bytes"]


def test_baseline_numbers_are_saved():
    assert BASELINE.exists(), "run tools/map_load_test.py --out " + str(BASELINE.relative_to(ROOT))
    rep = json.loads(BASELINE.read_text(encoding="utf-8"))
    cells = {(r["pins"], r["profile"]): r for r in rep["results"]}
    for n in (200, 1000, 5000, 10000):
        for prof in ("computer", "phone"):
            row = cells.get((n, prof))
            assert row, (n, prof)
            for k in ("html_bytes", "html_gzip_bytes", "usable_ms", "pan_frame_p95_ms"):
                assert isinstance(row.get(k), (int, float)), (n, prof, k)
            assert not row.get("failed"), (n, prof, row.get("failed"))
    today = [r for r in rep["results"] if r["label"] == "today"]
    assert {r["profile"] for r in today} == {"computer", "phone"}


# ── Browser checks (opt-in) ────────────────────────────────────────────────────

browser = pytest.mark.skipif(not UI_BASE, reason="set FORGE_UI_BASE to run the browser checks")


@browser
def test_browser_nothing_missing_at_both_sizes():
    pytest.importorskip("playwright.sync_api")
    import nothing_missing_check as nmc
    rep = nmc.run(UI_BASE, log=None)
    assert rep["problems"] == [], rep["problems"][:10]
    for size, ok in rep["sizes"].items():
        assert all(v == rep["records"] for v in ok.values()), (size, ok)


@browser
@pytest.mark.parametrize("day,first", [("2026-10-31", "25 Oct"), ("2026-12-15", "9 Dec")])
def test_browser_sees_the_frozen_day(day, first):
    """The page's own code works out 'today' from the helper's date."""
    sync_api = pytest.importorskip("playwright.sync_api")
    data = nm.load_dist(DIST) if _have_dist() else None
    if not data:
        pytest.skip("dist not built")
    m = next(x for x in data["markers"] if x.get("lat") is not None)
    state = json.dumps({"v": 1, "lat": round(m["lat"], 4), "lng": round(m["lng"], 4), "label": "clock check",
                        "kind": "", "radius": 2000, "dates": "7", "on": True, "remember": False})
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context()
        ms = forge_clock.freeze_today(ctx, day)
        ctx.route("**/*", lambda r: r.abort() if "tile.openstreetmap.org" in r.request.url else r.continue_())
        ctx.add_init_script(f"try {{ sessionStorage.setItem('zad.areaCheck.v1', {json.dumps(state)}); }} catch (e) {{}}")
        page = ctx.new_page()
        page.goto(UI_BASE, wait_until="load")
        assert abs(page.evaluate("() => Date.now()") - ms) < 5000
        page.wait_for_function("() => (document.getElementById('ac-summary') || {}).textContent")
        summary = page.locator("#ac-summary").text_content()
        d = int(day[-2:])
        assert f"between {first} and {d} {first.split()[1]}" in summary, summary
        b.close()

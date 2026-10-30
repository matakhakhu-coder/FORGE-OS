"""Time-aware pins, Batch 7: the still age ring, the Updated mark, and the
drawer and map-key wording.

The ring steps down through four bands across the 30-day live window, from
the incident day in South African time. The Updated mark shows on the day of
an approved material update and the 7 days after, and never changes the ring.
The drawer reads "Happened [date], added [date], last updated [date]" and
keeps "Updated [date]: [note]" after the mark goes.

The browser half (tests/browser/age_marks_check.py) runs only with
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
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "browser"))

from tools import map_archive as ma  # noqa: E402
from tools import map_data  # noqa: E402
from tools import publish  # noqa: E402

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


def _at(day: str) -> str:
    return day + "T12:00:00+02:00"


# ── The ring's bands ─────────────────────────────────────────────────────────────────────

@needs_node
def test_ring_band_at_each_edge():
    ages = [-3, 0, 1, 2, 3, 7, 8, 14, 15, 29, 30, 45]
    days = [(date(2026, 11, 20) - timedelta(days=a)).isoformat() for a in ages]
    got = _node("""console.log(JSON.stringify(DATA.map(d => {
        const b = F.ageBand({incident_start: d}, Date.parse('2026-11-20T12:00:00+02:00'));
        return b ? b.band : null; })))""", days)
    assert got == [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, None, None]


@needs_node
def test_ring_counts_the_south_african_day():
    # 22:30 UTC on 20 October is 00:30 on 21 October in South Africa.
    got = _node("""const p = {incident_start: '2026-10-20T22:30:00Z'};
        console.log(JSON.stringify(['2026-10-23', '2026-10-24'].map(d =>
          F.ageBand(p, Date.parse(d + 'T12:00:00+02:00')).band)))""", None)
    assert got == [0, 1]   # age 2 on 23 October, 3 on 24 October


@needs_node
def test_no_date_no_ring():
    got = _node("console.log(JSON.stringify([F.ageBand({}), F.ageBand(null), F.ageBand({incident_start: 'x'})]))",
                None)
    assert got == [None, None, None]


@needs_node
def test_bands_match_the_python_rule_every_day_for_real_pins(real_pins):
    from age_marks_check import expected_band
    days, d = [], date(2026, 10, 1)
    while d <= date(2026, 12, 1):
        days.append(d)
        d += timedelta(days=1)
    pins = [{k: p.get(k) for k in ("incident_start", "incident_at", "published_at")} for p in real_pins]
    js = _node("""console.log(JSON.stringify(DATA.days.map(d => DATA.pins.map(p => {
        const b = F.ageBand(p, Date.parse(d + 'T12:00:00+02:00')); return b ? String(b.band) : 'none'; }))))""",
               {"days": [x.isoformat() for x in days], "pins": pins})
    py = [[expected_band(p, x) for p in real_pins] for x in days]
    assert len(real_pins) == 78 and js == py


# ── The Updated mark ───────────────────────────────────────────────────────────────────

@needs_node
def test_updated_mark_shows_on_day_7_and_is_gone_on_day_8():
    days = [(date(2026, 10, 24) + timedelta(days=n)).isoformat() for n in range(0, 10)]
    got = _node("""const p = {incident_start: '2026-10-05', last_material_update_at: '2026-10-24T09:00:00+02:00'};
        console.log(JSON.stringify(DATA.map(d => F.showUpdated(p, Date.parse(d + 'T12:00:00+02:00')))))""", days)
    assert got == [True] * 8 + [False] * 2   # 24 to 31 October, gone on 1 November


@needs_node
def test_updated_mark_counts_the_south_african_day():
    # 22:30 UTC on 20 October is 21 October in South Africa: shows to 28 October.
    got = _node("""const p = {last_material_update_at: '2026-10-20T22:30:00Z'};
        console.log(JSON.stringify(['2026-10-28', '2026-10-29'].map(d =>
          F.showUpdated(p, Date.parse(d + 'T12:00:00+02:00')))))""", None)
    assert got == [True, False]


@needs_node
def test_no_update_no_mark_and_an_update_never_moves_the_ring():
    days = [(date(2026, 10, 1) + timedelta(days=n)).isoformat() for n in range(0, 45)]
    got = _node("""const a = {incident_start: '2026-10-03'};
        const b = Object.assign({last_material_update_at: '2026-10-20T09:00:00+02:00'}, a);
        console.log(JSON.stringify(DATA.map(d => { const t = Date.parse(d + 'T12:00:00+02:00');
          const x = F.ageBand(a, t), y = F.ageBand(b, t);
          return [F.showUpdated(a, t), x ? x.band : null, y ? y.band : null, F.isLive(a, t) === F.isLive(b, t)]; })))""",
                days)
    assert all(not r[0] and r[1] == r[2] and r[3] for r in got)


# ── The words ─────────────────────────────────────────────────────────────────────────

@needs_node
def test_dates_line_and_age_words():
    got = _node("""const t = Date.parse('2026-10-24T12:00:00+02:00');
        const full = {incident_start: '2026-10-12T21:00:00Z', published_at: '2026-10-19T08:00:00+02:00',
                      last_material_update_at: '2026-10-24T09:00:00+02:00'};
        console.log(JSON.stringify([
          F.datesLine(full, t),
          F.datesLine({incident_start: '2026-10-12', published_at: '2026-10-19'}, t),
          F.datesLine({published_at: '2026-10-19'}, t),
          F.datesLine({incident_start: '2025-12-30', published_at: '2026-01-02'}, t),
          F.datesLine({}, t),
          F.ageWords({incident_start: '2026-10-24'}, t), F.ageWords({incident_start: '2026-10-23'}, t),
          F.ageWords({incident_start: '2026-10-12'}, t), F.ageWords({}, t),
          F.shortDate('2026-10-05', t), F.shortDate('2025-10-05', t)]))""", None)
    assert got == [
        "Happened 12 Oct, added 19 Oct, last updated 24 Oct",   # 21:00 UTC is 23:00 on 12 Oct in SA
        "Happened 12 Oct, added 19 Oct",
        "Added 19 Oct",                     # no incident date: never says Happened
        "Happened 30 Dec 2025, added 2 Jan",
        "",
        "today", "yesterday", "12 days ago", "",
        "5 Oct", "5 Oct 2025",
    ]


# ── The build carries what the marks need ─────────────────────────────────────────────

def test_map_list_carries_the_update_date_and_the_details_carry_the_note():
    assert "last_material_update_at" in map_data.MAP_FIELDS
    assert "update_note" not in map_data.MAP_FIELDS      # the drawer loads it with the details
    got = publish._material_update({
        "last_material_update_at": "2026-10-24T09:00:00+02:00", "update_note": " Arrest made. ",
        "update_sources": [{"name": "News", "url": "https://news.example/a"},
                           {"name": "Bad", "url": "javascript:alert(1)"}, "junk"]})
    assert got["last_material_update_at"] == "2026-10-24T09:00:00+02:00"
    assert got["update_note"] == "Arrest made."
    assert [s["url"] for s in got["update_sources"]] == ["https://news.example/a"]
    assert publish._material_update({"last_material_update_at": "2026-10-24"}) == {
        "last_material_update_at": None, "update_note": None, "update_sources": []}
    assert publish._material_update({}) == {
        "last_material_update_at": None, "update_note": None, "update_sources": []}


def test_every_real_pin_loads_with_no_update(real_pins):
    assert len(real_pins) == 78
    assert all(p.get("last_material_update_at") is None for p in real_pins)


# ── The template ───────────────────────────────────────────────────────────────────────

def _css_rule(html: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", html)
    assert m, selector
    return m.group(1)


def test_ring_and_mark_are_still():
    html = TEMPLATE.read_text(encoding="utf-8")
    for sel in (".sig-ring", ".sig-ring--0", ".sig-ring--1", ".sig-ring--2", ".sig-ring--3", ".sig-upd"):
        rule = _css_rule(html, sel)
        assert "animation" not in rule and "transition" not in rule, sel


def test_bands_differ_by_width_or_dash_not_only_by_fade():
    html = TEMPLATE.read_text(encoding="utf-8")
    looks = []
    for b in range(4):
        rule = _css_rule(html, f".sig-ring--{b}")
        width = re.search(r"border-width:\s*([\d.]+)px", rule).group(1)
        style = re.search(r"border-style:\s*(\w+)", rule).group(1)
        assert float(width) == int(float(width)), "whole pixels: browsers round 1.5px down"
        looks.append((width, style))
    assert len(set(looks)) == 4


def test_map_key_names_every_band_the_archive_and_the_updated_mark():
    html = TEMPLATE.read_text(encoding="utf-8")
    key = html[html.index('id="age-key"'):html.index("</section>", html.index('id="age-key"'))]
    for words in ("0 to 2 days ago", "3 to 7 days ago", "8 to 14 days ago", "15 to 29 days ago",
                  "No ring: older than the 30-day live map", "month's archive",
                  "White diamond: updated in the last 7 days", "An update never changes the ring",
                  "The dot's colour is its category"):
        assert words in key, words
    # Six rows from Batch 7, plus Batch 8's pulse and numbered-circle rows.
    assert key.count('class="age-key__row"') == 8
    assert "crime rate" not in key.lower()


def test_drawer_and_preview_say_the_marks_in_words():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'id="sd-dates"' in html and 'id="sd-updated"' in html
    assert "FP.datesLine(m)" in html
    assert "'Updated ' + FP.shortDate(updDay) + ': ' + m.update_note" in html
    assert "smap-popup__age" in html
    # The Batch 5 archive note keeps its wording.
    assert "' archive. Older than 30 days, so it isn\\u2019t on the live map.'" in html
    # Marks are redrawn when the day changes.
    assert "refreshAgeMark(ml.idx);" in html


# ── Browser ──────────────────────────────────────────────────────────────────────────

BROWSER = os.environ.get("FORGE_UI_BASE")
needs_browser = pytest.mark.skipif(not BROWSER, reason="browser checks are off (set FORGE_UI_BASE)")


@pytest.fixture(scope="module")
def dec_build(tmp_path_factory):
    from time_aware_dists import build
    return build(tmp_path_factory.mktemp("ta7dec"), date(2026, 12, 15))


@pytest.fixture(scope="module")
def real_pins(dec_build):
    return ma.all_entries(dec_build)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from time_aware_dists import build
    from age_marks_check import make_upd
    out = tmp_path_factory.mktemp("ta7dists")
    for name, day in (("oct", date(2026, 10, 24)), ("dec", date(2026, 12, 15))):
        dist = build(tmp_path_factory.mktemp("ta7" + name), day)
        shutil.copytree(dist, out / name)
    make_upd(out / "oct", out / "upd")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield out, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_browser
def test_browser_age_ring_updated_mark_and_wording(served):
    import age_marks_check
    out, base = served
    rep = age_marks_check.run(base, out)
    assert rep["problems"] == []
    assert all(all(r.values()) for r in rep["results"].values())

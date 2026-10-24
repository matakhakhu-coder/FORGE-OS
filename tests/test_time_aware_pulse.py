"""Time-aware pins, Batch 8: the newest-pin pulse and the Motion switch.

A pin pulses for 48 hours after it was added to the map (published_at),
while it is on the 30-day live map. It needs no gravity score. An approved
material update shows the diamond but never starts or extends the pulse, and
routine checks change nothing. Each pin's place in the breath comes from its
id, so pins added together don't pulse in step. The pulse is a smooth sine
breath of size and opacity on a halo outside the still age ring. Motion is on
by default; the Motion switch turns it off and is remembered, and the
device's reduce-motion setting always turns it off. With motion off the halo
stays, still. Numbered circles never pulse: one holding a new pin gets a
still white dot.

The browser half (tests/browser/pulse_check.py) runs only with FORGE_UI_BASE
set, like the other browser checks.
"""
from __future__ import annotations

import functools
import http.server
import json
import math
import os
import re
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

from tools import map_archive as ma  # noqa: E402

JS = ROOT / "publisher" / "static" / "js" / "forge-pins.js"
TEMPLATE = ROOT / "publisher" / "templates" / "map.html"
PRIVACY = ROOT / "publisher" / "templates" / "privacy.html"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")
SAST = timezone(timedelta(hours=2))
H = 3600 * 1000


def _node(script: str, payload) -> object:
    prog = ("const F = require(%s).ForgePins; const DATA = %s;\n%s"
            % (json.dumps(str(JS)), json.dumps(payload), script))
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SAST)
    return int(dt.timestamp() * 1000)


def _pin(published, incident="2026-10-23", sid="aaaa1111-0000-4000-8000-000000000001", **kw):
    return {"signal_id": sid, "published_at": published, "incident_start": incident, **kw}


# ── Which pins pulse, and for how long ────────────────────────────────────────────

@needs_node
def test_new_for_exactly_48_hours_after_it_was_added():
    added = _ms("2026-10-24T10:00:00+02:00")
    nows = [added - H, added, added + 47 * H, added + 48 * H - 1, added + 48 * H, added + 72 * H]
    got = _node("console.log(JSON.stringify(DATA.nows.map(n => F.isNew(DATA.pin, n))))",
                {"pin": _pin("2026-10-24T10:00:00+02:00", "2026-10-24"), "nows": nows})
    # an hour "before" it was added (a visitor whose clock is behind) still counts as new
    assert got == [True, True, True, True, False, False]


@needs_node
def test_added_time_reads_offsets_utc_and_bare_dates():
    got = _node("console.log(JSON.stringify(DATA.map(v => F.instantMs(v))))",
                ["2026-10-23T16:05:00Z", "2026-10-23T18:05:00+02:00", "2026-10-23T18:05:00",
                 "2026-10-23", "not a date", ""])
    a = _ms("2026-10-23T16:05:00Z")
    assert got[:3] == [a, a, a]
    assert got[3] == _ms("2026-10-23T00:00:00+02:00")
    assert got[4:] == [None, None]


@needs_node
def test_only_live_pins_pulse_and_no_gravity_needed():
    now = _ms("2026-10-24T12:00:00+02:00")
    pins = [_pin("2026-10-24T09:00:00+02:00", "2026-10-23"),               # new and live
            _pin("2026-10-24T09:00:00+02:00", "2026-09-01"),               # new, but older than 30 days
            _pin("2026-10-24T09:00:00+02:00", "2026-10-23", gravity_score=None),
            _pin("2026-10-24T09:00:00+02:00", "2026-10-23", gravity_score=0.0),
            _pin(None, "2026-10-23"),                                       # no added time
            _pin("2026-10-20T09:00:00+02:00", "2026-10-20")]               # added four days ago
    got = _node("console.log(JSON.stringify(DATA.pins.map(p => F.isNew(p, DATA.now))))",
                {"pins": pins, "now": now})
    assert got == [True, False, True, True, False, False]


@needs_node
def test_an_update_never_starts_or_extends_the_pulse():
    added = "2026-10-24T10:00:00+02:00"
    upd = "2026-10-25T09:00:00+02:00"
    after = _ms(added) + 48 * H + 60_000
    got = _node("console.log(JSON.stringify([F.isNew(DATA.a, DATA.t1), F.isNew(DATA.b, DATA.t1),"
                " F.showUpdated(DATA.b, DATA.t1), F.isNew(DATA.old, DATA.t2), F.showUpdated(DATA.old, DATA.t2),"
                " F.newUntil(DATA.a) === F.newUntil(DATA.b)]))",
                {"a": _pin(added, "2026-10-24"),
                 "b": _pin(added, "2026-10-24", last_material_update_at=upd),
                 "old": _pin("2026-10-10T10:00:00+02:00", "2026-10-09", last_material_update_at=upd),
                 "t1": after, "t2": _ms(upd) + H})
    # past 48 hours: no pulse with or without the update; the diamond shows
    assert got == [False, False, True, False, True, True]


@needs_node
def test_pulse_set_matches_python_for_real_pins_every_hour(real_pins):
    start = _ms("2026-10-01T00:00:00+02:00")
    nows = [start + h * H for h in range(0, 24 * 40, 1)]
    got = _node("console.log(JSON.stringify(DATA.nows.map(n => DATA.pins.filter(p => F.isNew(p, n))"
                ".map(p => p.signal_id).sort())))", {"pins": real_pins, "nows": nows})
    ever = set()
    for n, ids in zip(nows, got):
        today = datetime.fromtimestamp(n / 1000, SAST).date()
        # live: the Batch 5 rule (future or unreadable incident dates stay live);
        # new: added less than 48 hours before, or "after" a clock that is behind
        want = sorted(p["signal_id"] for p in real_pins
                      if p.get("published_at") and n - _ms(p["published_at"]) < 48 * H
                      and ((d := ma.incident_day(p)) is None or (today - d).days < 30))
        assert ids == want, datetime.fromtimestamp(n / 1000, SAST)
        ever.update(ids)
    assert ever, "some real pin should have pulsed in October"
    assert all(not p.get("gravity_score") for p in real_pins)


@needs_node
def test_phases_are_stable_spread_and_differ_for_pins_added_together():
    ids = [f"b8{i:06d}-0000-4000-8000-000000000{i:03d}" for i in range(200)]
    got = _node("console.log(JSON.stringify(DATA.map(s => F.pulsePhase({signal_id: s}))))", ids)
    again = _node("console.log(JSON.stringify(DATA.map(s => F.pulsePhase({signal_id: s.toUpperCase()}))))", ids)
    assert got == again                       # stable, and case doesn't matter
    assert all(0 <= p < 1 for p in got)
    assert len(set(got)) == len(got)
    # spread: each tenth of the breath holds 5% to 15% of 200 pins
    for k in range(10):
        n = sum(1 for p in got if k / 10 <= p < (k + 1) / 10)
        assert 10 <= n <= 30, (k, n)
    order = abs(sum(complex(math.cos(2 * math.pi * p), math.sin(2 * math.pi * p)) for p in got)) / len(got)
    assert order < 0.2


# ── The template ────────────────────────────────────────────────────────────────────────────

def _html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_old_gravity_pulse_is_gone():
    html = _html()
    for gone in ("sig-dot--pulse-critical", "sig-dot--pulse-active", "sig-pulse-bright", "sig-pulse-dim",
                 "HOURS_48", "@keyframes sig-ring"):
        assert gone not in html, gone
    # the dot never animates; only the halo does
    for m in re.finditer(r"\.sig-(dot|ring|upd)[^{]*\{([^}]*)\}", html):
        assert "animation" not in m.group(2), m.group(0)


def test_pulse_is_a_slow_sine_breath_of_size_and_opacity():
    html = _html()
    m = re.search(r"\.sig-pulse \{\s*animation: sig-breathe (\d+)ms cubic-bezier\(0\.37, 0, 0\.63, 1\) infinite;", html)
    assert m, "pulse uses the ease-in-out sine curve"
    assert int(m.group(1)) >= 2000              # never faster than one breath every 2 s
    assert 'PULSE_MS = ' + m.group(1) in JS.read_text(encoding="utf-8")
    kf = re.search(r"@keyframes sig-breathe \{(.*?)\n  \}", html, re.S).group(1)
    props = set(re.findall(r"([a-z-]+):", kf))
    assert props == {"transform", "opacity"}
    assert "0%, 100%" in kf                     # ends where it starts: no jump


def test_motion_off_and_reduce_motion_stop_it():
    html = _html()
    assert "html:not(.forge-motion-off) .leaflet-marker-icon .sig-pulse {" in html
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\) \{\s*\.leaflet-marker-icon \.sig-pulse \{([^}]*)\}", html)
    assert rm and "animation: none" in rm.group(1)
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in html
    assert "localStorage.setItem(MOTION_KEY, 'off')" in html
    assert "var MOTION_KEY = 'forge_map_motion';" in html


def test_motion_switch_markup():
    html = _html()
    m = re.search(r'<button class="md-ctrl md-ctrl--motion" id="ctrl-motion"[^>]*>', html)
    assert m
    tag = m.group(0)
    for attr in ('role="switch"', 'aria-checked="true"', 'aria-label="Motion"', 'aria-describedby="motion-desc"'):
        assert attr in tag, attr
    assert 'id="motion-status" role="status" aria-live="polite"' in html


def test_numbered_circles_get_a_still_dot_not_a_pulse():
    html = _html()
    assert "'<span class=\"mc-new\" aria-hidden=\"true\"></span>'" in html
    rule = re.search(r"\.marker-cluster \.mc-new \{([^}]*)\}", html).group(1)
    assert "animation" not in rule and "transition" not in rule


def test_newness_is_said_in_words_and_explained_in_the_key():
    html = _html()
    assert "bits.push('new on the map, added '" in html
    assert "ageBits.push('New: added '" in html
    key = re.search(r'<div class="age-key" id="age-key">(.*?)</div>\s*</div>', html, re.S)
    text = key.group(1) if key else html
    assert "added to the map in the last 48 hours" in text
    assert "Numbered circles never pulse" in text
    assert "The Motion switch" in text


def test_pulse_timing_is_locked_to_clock_and_id():
    html = _html()
    assert "anims.forEach(function (a) { a.startTime = -off; });" in html
    assert "var off = FP.pulsePhase(m) * FP.PULSE_MS;" in html


def test_privacy_page_names_the_motion_setting():
    assert "the Motion switch" in PRIVACY.read_text(encoding="utf-8")


# ── Fixtures and the browser half ─────────────────────────────────────────────────────────────────

BROWSER = os.environ.get("FORGE_UI_BASE")
needs_browser = pytest.mark.skipif(not BROWSER, reason="browser checks are off (set FORGE_UI_BASE)")


@pytest.fixture(scope="module")
def dec_build(tmp_path_factory):
    from time_aware_dists import build
    return build(tmp_path_factory.mktemp("ta8dec"), date(2026, 12, 15))


@pytest.fixture(scope="module")
def real_pins(dec_build):
    return ma.all_entries(dec_build)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from time_aware_dists import build
    from age_marks_check import make_upd
    from pulse_check import make_burst
    out = tmp_path_factory.mktemp("ta8dists")
    for name, day in (("oct", date(2026, 10, 24)), ("dec", date(2026, 12, 15))):
        dist = build(tmp_path_factory.mktemp("ta8" + name), day)
        shutil.copytree(dist, out / name)
    make_upd(out / "oct", out / "upd")
    make_burst(out / "oct", out / "burst")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield out, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_browser
def test_browser_pulse_motion_switch_and_no_strobe(served):
    import pulse_check
    out, base = served
    rep = pulse_check.run(base, out)
    assert rep["problems"] == []
    assert all(all(r.values()) for r in rep["results"].values())

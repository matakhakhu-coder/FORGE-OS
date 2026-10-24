"""Newest-pin pulse and the Motion switch (time-aware pins, Batch 8), in a real browser.

A pin pulses for 48 hours after it was added to the map (published_at),
while it is on the 30-day live map. This checks, on a computer and a phone:

  which      with today frozen (forge_clock), the pins that pulse are exactly
             the live pins added in the 48 hours before, worked out here in
             Python; none of them needs a gravity score
  stops      a tab left open: moving the clock past a pin's 48 hours drops
             its pulse without a reload (ZADPulse.tick, and on a computer the
             page's own minute timer); the midnight rollover keeps the pulse
             set right and the timing locked
  update     an approved material update shows the diamond but never starts
             or extends a pulse (upd/ build and burst/ pin 0)
  motion     the Motion switch stops every pin animation, leaves a still
             halo, is remembered after a reload, and turns back on in step;
             with the device set to reduce motion it is off and stays off
  strobe     on burst/ (50 made-up pins added at the same moment in one
             341 x 256 patch): pulse phases spread out, the patch's mean
             light barely changes across a breath (and far less than the
             same 50 forced into step), and frames stay smooth
  words      the hover text, preview, map key and switch say it in words
  archive    an older pin opened from its month file never pulses and keeps
             the Batch 5 archive note

    python tests/browser/pulse_check.py --base http://127.0.0.1:8858/ --dists /tmp/ta8

--dists is the folder --base serves, holding oct/, dec/, upd/ and burst/.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import forge_clock  # noqa: E402
from tools import map_archive as ma  # noqa: E402
from tools import map_data  # noqa: E402

SIZES = {"computer": {"viewport": {"width": 1366, "height": 900}},
         "phone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}}
NEW_MS = 48 * 3600 * 1000
PULSE_MS = 3200
SAST = timezone(timedelta(hours=2))

# The burst fixture: 50 pins added at 10:00 on 24 October, in a 10 x 5 grid
# about 29 px apart at zoom 14, centred here.
BURST_AT = "2026-10-24T10:00:00+02:00"
BURST_CENTRE = (-34.0310, 18.5560)
BURST_STEP = 0.0025
BURST_ZOOM = 14          # clustering stops at 14; the grid is about 290 x 175 px
BURST_UPDATE = {"at": "2026-10-25T09:00:00+02:00",
                "note": "Test update: a third suspect was arrested.",
                "sources": [{"name": "Example News", "url": "https://example.org/b8",
                             "domain": "example.org"}]}

ANIMS_JS = ("() => document.getAnimations().filter(a => a.effect && a.effect.target"
            " && a.effect.target.closest && a.effect.target.closest('.leaflet-marker-icon, .marker-cluster'))"
            ".map(a => ({ cls: a.effect.target.className, state: a.playState,"
            " t: a.currentTime, tl: document.timeline.currentTime }))")


def instant_ms(value: str) -> int | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SAST)
    return int(dt.timestamp() * 1000)


def is_live(entry: dict, today: date) -> bool:
    """The Batch 5 rule: incident today or in the 29 days before; a future or
    unreadable date stays live."""
    d = ma.incident_day(entry)
    return d is None or (today - d).days < 30


def expected_new(entries: list[dict], now_ms: int) -> set[str]:
    """Live pins added less than 48 hours before now_ms. A pin added 'after'
    now (a visitor whose clock is behind) counts as new."""
    today = datetime.fromtimestamp(now_ms / 1000, SAST).date()
    out = set()
    for e in entries:
        a = instant_ms(e.get("published_at") or "")
        if a is not None and now_ms - a < NEW_MS and is_live(e, today):
            out.add(e["signal_id"][:8])
    return out


def make_burst(oct_dist: Path, out: Path) -> list[str]:
    """Copy the October build and add 50 made-up pins added at one moment.

    Each copies a real live pin's fields, with its own id, a place in a tight
    grid and incident dates of 23 or 24 October. Pin 0 also gets an approved
    material update on 25 October. Changes only the page's map list and adds
    50 detail files.
    """
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(oct_dist, out)
    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    m = re.search(r"var RAW = (\[.*?\]);\n", html)
    raw = json.loads(m.group(1))
    template = next(e for e in raw if e.get("incident_start") and e.get("lat"))
    tdetail = json.loads((out / "pins" / f"{template['signal_id'][:8]}.json").read_text(encoding="utf-8"))
    ids = []
    for i in range(50):
        row, col = divmod(i, 10)
        sid = f"b8{i:06d}-0000-4000-8000-000000000{i:03d}"
        e = dict(template)
        day = "2026-10-23" if i % 2 else "2026-10-24"
        e.update(signal_id=sid, title=f"Burst test pin {i + 1}",
                 lat=round(BURST_CENTRE[0] + (row - 2) * BURST_STEP, 6),
                 lng=round(BURST_CENTRE[1] + (col - 4.5) * BURST_STEP, 6),
                 published_at=BURST_AT, incident_start=day, incident_end=day,
                 last_material_update_at=None)
        d = dict(tdetail)
        d.update(signal_id=sid, title=e["title"], published_at=BURST_AT,
                 incident_start=day, incident_end=day,
                 last_material_update_at=None, update_note=None, update_sources=[])
        if i == 0:
            e["last_material_update_at"] = BURST_UPDATE["at"]
            d.update(last_material_update_at=BURST_UPDATE["at"], update_note=BURST_UPDATE["note"],
                     update_sources=BURST_UPDATE["sources"])
        (out / "pins" / f"{sid[:8]}.json").write_text(json.dumps(d), encoding="utf-8")
        raw.append(e)
        ids.append(sid[:8])
    html = html[:m.start(1)] + json.dumps(raw) + html[m.end(1):]
    index.write_text(html, encoding="utf-8")
    return ids


def _open(browser, size, url, day, at="12:00", **ctx_kw):
    ctx = browser.new_context(**SIZES[size], **ctx_kw)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    forge_clock.freeze_today(page, day, at)
    page.goto(url, wait_until="load")
    page.wait_for_function("() => window.ZADPulse && window.ZADLive && window.ForgePins")
    page.wait_for_timeout(400)
    return ctx, page, errors


def _set_now(page, ms: int):
    # a bare number would be read as seconds; pass the moment itself
    page.clock.set_fixed_time(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


def _phases(page) -> list[dict]:
    """Each drawn pulse: its title's id, its phase now, and the phase its id asks for."""
    return page.evaluate(
        "P => [...document.querySelectorAll('.leaflet-marker-icon .sig-pulse')].map(el => {"
        " const a = el.getAnimations()[0]; const ic = el.closest('.leaflet-marker-icon');"
        " return { title: ic.getAttribute('title') || '', running: !!a && a.playState === 'running',"
        " phase: a ? ((a.currentTime % P) + P) % P / P : null,"
        " clock: ((document.timeline.currentTime % P) + P) % P / P }; })", PULSE_MS)


def _circ_gap(a: float, b: float) -> float:
    d = abs(a - b) % 1
    return min(d, 1 - d)


def _patch_lum(png: bytes) -> float:
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    px = list(img.get_flattened_data()) if hasattr(img, 'get_flattened_data') else list(img.getdata())

    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    tot = 0.0
    for r, g, b in px[::3]:
        tot += 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
    return tot / len(px[::3])


def _frames(page, ms=3000) -> dict:
    gaps = page.evaluate(
        "ms => new Promise(res => { const g = []; let last = null; const t0 = performance.now();"
        " function f(t) { if (last !== null) g.push(t - last); last = t;"
        " if (t - t0 < ms) requestAnimationFrame(f); else res(g); } requestAnimationFrame(f); })", ms)
    gaps.sort()
    return {"frames": len(gaps), "median_ms": round(statistics.median(gaps), 1),
            "p95_ms": round(gaps[int(len(gaps) * 0.95) - 1], 1), "max_ms": round(gaps[-1], 1),
            "over_50ms": sum(1 for g in gaps if g > 50)}


def run(base: str, dists: Path, sizes=("computer", "phone")) -> dict:
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/") + "/"
    oct_list = map_data.read_map_list(dists / "oct" / "index.html")
    upd_list = map_data.read_map_list(dists / "upd" / "index.html")
    burst_list = map_data.read_map_list(dists / "burst" / "index.html")
    burst_ids = {e["signal_id"][:8] for e in burst_list if e["signal_id"].startswith("b8")}
    results: dict[str, dict[str, bool]] = {}
    problems: list[str] = []
    numbers: dict = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for size in sizes:
            res = results.setdefault(size, {})
            num = numbers.setdefault(size, {})
            errs_all: list[str] = []

            def check(name, cond, detail=""):
                print(f"  {size}: {'ok  ' if cond else 'FAIL'} {name} {'' if cond else detail}", flush=True)
                res[name] = bool(cond)
                if not cond:
                    problems.append(f"{size}: {name} {detail}".strip())

            # ── which pins pulse, on pretend dates ──────────────────────────
            check("no pin has a gravity score", all(not e.get("gravity_score") for e in oct_list))
            for day, at in (("2026-10-24", "12:00"), ("2026-10-25", "12:00"), ("2026-10-23", "18:00"),
                            ("2026-10-31", "12:00"), ("2026-11-23", "12:00"), ("2026-12-15", "12:00")):
                ctx, page, errs = _open(browser, size, base + "oct/", day, at)
                now = forge_clock.epoch_ms(day, at)
                want = expected_new(oct_list, now)
                got = set(page.evaluate("() => ZADPulse.pulsing()"))
                live = set(page.evaluate("() => ZADLive.live()"))
                num.setdefault("pulsing_by_date", {})[f"{day} {at}"] = len(got)
                check(f"pulse set {day} {at}", got == want, f"got {sorted(got - want)} missing {sorted(want - got)}")
                check(f"only live pins pulse {day}", got <= live)
                # every drawn halo belongs to a pin whose words say new, and back
                bad = page.evaluate(
                    "() => [...document.querySelectorAll('.leaflet-marker-icon.leaflet-interactive, .leaflet-marker-icon')]"
                    ".filter(ic => ic.querySelector('.sig-pin'))"
                    ".filter(ic => !!ic.querySelector('.sig-pulse') !== /new on the map, added/.test(ic.getAttribute('title') || ''))"
                    ".length")
                check(f"halo matches words {day}", bad == 0, f"{bad} mismatched")
                errs_all += errs
                ctx.close()

            # ── stops after 48 hours in an open tab; midnight keeps it right ─
            stamps = sorted({instant_ms(e["published_at"]) for e in oct_list if e.get("published_at")})
            # the last pin added before 23 Oct 12:00 SAST: its 48 h end on the 25th
            target = max(s for s in stamps if s < forge_clock.epoch_ms("2026-10-23", "12:00"))
            end = target + NEW_MS
            ctx, page, errs = _open(browser, size, base + "oct/", "2026-10-24", "12:00")
            _set_now(page, end - 30_000)
            page.evaluate("() => ZADPulse.tick()")
            before = set(page.evaluate("() => ZADPulse.pulsing()"))
            check("before 48 h", before == expected_new(oct_list, end - 30_000))
            _set_now(page, end + 30_000)
            if size == "computer":
                # the page's own minute timer, not the test hook
                page.wait_for_function("n => ZADPulse.pulsing().length < n", arg=len(before), timeout=65_000)
            else:
                page.evaluate("() => ZADPulse.tick()")
            after = set(page.evaluate("() => ZADPulse.pulsing()"))
            gone = before - after
            check("stops at 48 h without reload", after == expected_new(oct_list, end + 30_000) and gone,
                  f"gone {sorted(gone)}")
            num["stopped_at_48h"] = len(gone)
            # midnight: 25 Oct 23:59:30 -> 26 Oct 00:00:30
            m1 = forge_clock.epoch_ms("2026-10-25", "23:59") + 30_000
            _set_now(page, m1)
            page.evaluate("() => ZADPulse.tick()")
            s1 = set(page.evaluate("() => ZADPulse.pulsing()"))
            _set_now(page, m1 + 60_000)
            page.evaluate("() => ZADPulse.tick()")
            s2 = set(page.evaluate("() => ZADPulse.pulsing()"))
            check("midnight rollover pulse set", s1 == expected_new(oct_list, m1)
                  and s2 == expected_new(oct_list, m1 + 60_000), f"{len(s1)} -> {len(s2)}")
            check("midnight moved the day", page.evaluate("() => ZADLive.today()") == "2026-10-26")
            errs_all += errs
            ctx.close()

            # ── numbered circles: still dot, never animated ─────────────────
            ctx, page, errs = _open(browser, size, base + "oct/", "2026-10-24", "12:00")
            dots = page.evaluate("() => document.querySelectorAll('.marker-cluster--new .mc-new').length")
            cl_anims = page.evaluate(
                "() => document.getAnimations().filter(a => a.effect && a.effect.target"
                " && a.effect.target.closest && a.effect.target.closest('.marker-cluster')).length")
            num["clusters_with_new_dot"] = dots
            check("some numbered circles show the still dot", dots > 0)
            check("numbered circles never animate", cl_anims == 0)
            _set_now(page, forge_clock.epoch_ms("2026-10-31", "12:00"))
            page.evaluate("() => ZADPulse.tick()")
            page.wait_for_timeout(300)
            dots2 = page.evaluate("() => document.querySelectorAll('.marker-cluster--new').length")
            check("dots go when nothing is new", dots2 == 0 and page.evaluate("() => ZADPulse.pulsing().length") == 0)
            errs_all += errs
            ctx.close()

            # ── an update never starts or extends a pulse ───────────────────
            for u in [e for e in upd_list if e.get("last_material_update_at")]:
                day = str(ma.local_day(u["last_material_update_at"]))
                ctx, page, errs = _open(browser, size, base + "upd/", day, "23:00")
                got = set(page.evaluate("() => ZADPulse.pulsing()"))
                now = forge_clock.epoch_ms(day, "23:00")
                # the pulse follows when the pin was added, whatever the update
                check(f"update {u['signal_id'][:8]} leaves the pulse alone",
                      (u["signal_id"][:8] in got) == (u["signal_id"][:8] in expected_new(upd_list, now)))
                errs_all += errs
                ctx.close()
            b0 = sorted(burst_ids)[0]
            ctx, page, errs = _open(browser, size, base + "burst/", "2026-10-25", "12:00")
            p25 = set(page.evaluate("() => ZADPulse.pulsing()"))
            _set_now(page, instant_ms(BURST_AT) + NEW_MS + 60_000)
            page.evaluate("() => ZADPulse.tick()")
            p26 = set(page.evaluate("() => ZADPulse.pulsing()"))
            marks = {r["id"]: r for r in page.evaluate("() => window.ZADAge ? ZADAge.marks() : []")}
            check("updated new pin still stops 48 h after it was added",
                  b0 in p25 and b0 not in p26 and not (burst_ids & p26))
            if marks:
                check("its diamond stays after the pulse ends", bool(marks.get(b0, {}).get("updated")),
                      str(marks.get(b0)))
            errs_all += errs
            ctx.close()

            # ── Motion switch: off, still, remembered, back in step ─────────
            ctx, page, errs = _open(browser, size, base + "burst/", "2026-10-24", "12:00")
            page.evaluate("([la, ln, z]) => ZADAge.view(la, ln, z)",
                          [*BURST_CENTRE, BURST_ZOOM])
            page.wait_for_timeout(700)
            sw = page.locator("#ctrl-motion")
            box = sw.bounding_box()
            check("switch visible", sw.is_visible() and box and box["width"] >= 24 and box["height"] >= 24
                  and box["x"] >= 0 and box["x"] + box["width"] <= SIZES[size]["viewport"]["width"], str(box))
            overlaps = page.evaluate(
                "() => { const r = document.getElementById('ctrl-motion').getBoundingClientRect();"
                " return [...document.querySelectorAll('.md-ctrl, .md-topbar button, header a, header button')]"
                ".filter(e => e.id !== 'ctrl-motion' && e.offsetParent).filter(e => { const q = e.getBoundingClientRect();"
                " return q.left < r.right - 1 && q.right > r.left + 1 && q.top < r.bottom - 1 && q.bottom > r.top + 1; })"
                ".map(e => e.id || e.className); }")
            check("switch overlaps nothing", not overlaps, str(overlaps))
            a11y = page.evaluate(
                "() => { const b = document.getElementById('ctrl-motion'); return { role: b.getAttribute('role'),"
                " name: b.getAttribute('aria-label'), checked: b.getAttribute('aria-checked'),"
                " desc: (document.getElementById(b.getAttribute('aria-describedby')) || {}).textContent }; }")
            check("switch role and name", a11y["role"] == "switch" and a11y["name"] == "Motion"
                  and a11y["checked"] == "true" and "pulse" in (a11y["desc"] or ""), str(a11y))
            on_anims = [a for a in page.evaluate(ANIMS_JS) if a["state"] == "running"]
            num["burst_running_pulses"] = len(on_anims)
            check("burst: 50 pulses running", len(on_anims) >= 50, str(len(on_anims)))
            # phases: locked to the clock and the id, and spread out
            ph = [p for p in _phases(page) if "Burst test pin" in p["title"]]
            spread = [p["phase"] for p in ph if p["running"]]
            if spread:
                worst_window = max(sum(1 for q in spread if _circ_gap(q, c / 100) <= 0.05) for c in range(100))
                order = abs(sum(complex(math.cos(2 * math.pi * q), math.sin(2 * math.pi * q))
                                for q in spread)) / len(spread)
                num["burst_phase"] = {"pins": len(spread), "most_in_any_tenth_of_a_breath": worst_window,
                                      "in_step_score_0_to_1": round(order, 3)}
                # modelled halo light (opacity x area) across a breath, vs all 50 in step
                def light(x):
                    c = math.cos(2 * math.pi * x)
                    return (0.34 + 0.26 * c) * (1.3 - 0.3 * c) ** 2
                ts = [k / 64 for k in range(64)]
                ours_m = [sum(light(q + t) for q in spread) for t in ts]
                step_m = [len(spread) * light(t) for t in ts]
                num["burst_phase"]["modelled_light_swing_vs_in_step"] = round(
                    (max(ours_m) - min(ours_m)) / (max(step_m) - min(step_m)), 3)
                check("burst phases spread", worst_window <= len(spread) * 0.25 and order < 0.35,
                      json.dumps(num["burst_phase"]))
            # light in the 341 x 256 patch across one breath, ours vs forced in step
            vw, vh = SIZES[size]["viewport"]["width"], SIZES[size]["viewport"]["height"]
            clip = {"x": max(0, vw / 2 - 170.5), "y": max(0, vh / 2 - 128), "width": min(341, vw), "height": 256}

            def lum_series(n=20):
                vals = []
                for _ in range(n):
                    vals.append(_patch_lum(page.screenshot(clip=clip)))
                    page.wait_for_timeout(max(0, PULSE_MS // n - 60))
                return vals
            ours = lum_series()
            frames_on = _frames(page)
            page.evaluate("() => document.querySelectorAll('.leaflet-marker-icon .sig-pulse')"
                          ".forEach(el => el.getAnimations().forEach(a => { a.startTime = 0; }))")
            page.wait_for_timeout(100)
            step = lum_series()
            page.evaluate("() => ZADPulse.tick()")

            def rng(v):
                return max(v) - min(v)
            num["patch_light"] = {"mean": round(statistics.mean(ours), 4),
                                  "swing_ours": round(rng(ours), 5), "swing_forced_in_step": round(rng(step), 5),
                                  "largest_step_ours": round(max(abs(a - b) for a, b in zip(ours, ours[1:])), 5)}
            check("no strobe: patch light steady", rng(ours) < 0.5 * rng(step) and rng(ours) < 0.01,
                  json.dumps(num["patch_light"]))
            num["frames_motion_on"] = frames_on
            # resync after the forced test, then switch off
            sw.click()
            page.wait_for_timeout(300)
            off_anims = [a for a in page.evaluate(ANIMS_JS) if a["state"] == "running"]
            still = page.evaluate(
                "() => [...document.querySelectorAll('.leaflet-marker-icon .sig-pulse')].map(e => getComputedStyle(e).opacity)")
            status = page.evaluate("() => document.getElementById('motion-status').textContent")
            check("Motion off stops every pin animation", not off_anims, str(off_anims[:3]))
            check("Motion off keeps a still halo", still and all(abs(float(o) - 0.4) < 0.01 for o in still))
            check("Motion off announced and shown", page.evaluate(
                "() => document.getElementById('ctrl-motion').getAttribute('aria-checked')") == "false"
                and "Motion off" in status, status)
            num["frames_motion_off"] = _frames(page)
            # spiderfy / split animations off too
            check("cluster animations off", page.evaluate(
                "() => !!window.L && L.MarkerCluster.prototype._forgeMotionWrapped === true"))
            page.reload(wait_until="load")
            page.wait_for_function("() => window.ZADPulse")
            page.wait_for_timeout(400)
            check("Motion off remembered after reload", page.evaluate("() => ZADPulse.motionOn()") is False
                  and not [a for a in page.evaluate(ANIMS_JS) if a["state"] == "running"])
            # keyboard: Space turns it back on
            page.focus("#ctrl-motion")
            page.keyboard.press("Space")
            page.evaluate("([la, ln, z]) => ZADAge.view(la, ln, z)", [*BURST_CENTRE, BURST_ZOOM])
            page.wait_for_timeout(600)
            back = _phases(page)
            locked = [p for p in back if p["running"]]
            check("keyboard turns motion back on", page.evaluate("() => ZADPulse.motionOn()") is True and locked)
            errs_all += errs
            ctx.close()

            # phases locked to clock + id: check on a fresh burst load, twice
            ctx, page, errs = _open(browser, size, base + "burst/", "2026-10-24", "12:00")
            page.evaluate("([la, ln, z]) => ZADAge.view(la, ln, z)",
                          [*BURST_CENTRE, BURST_ZOOM])
            page.wait_for_timeout(500)
            want_phase = {r["title"]: r for r in page.evaluate(
                "() => [...document.querySelectorAll('.leaflet-marker-icon')].filter(ic => ic.querySelector('.sig-pulse'))"
                ".map(ic => ({ title: ic.getAttribute('title') }))")}
            ids_phase = page.evaluate(
                "ids => ids.map(i => ZADPulse.phase(i))", sorted(burst_ids))
            drift = []
            for p in _phases(page):
                if not p["running"]:
                    continue
                m = re.search(r"Burst test pin (\d+)", p["title"])
                if not m:
                    continue
                expect = (p["clock"] + ids_phase[int(m.group(1)) - 1]) % 1
                drift.append(_circ_gap(p["phase"], expect))
            # zoom out and back: redrawn pins keep their place in the breath
            page.evaluate("([la, ln]) => ZADAge.view(la, ln, 12)", list(BURST_CENTRE))
            page.wait_for_timeout(400)
            page.evaluate("([la, ln, z]) => ZADAge.view(la, ln, z)", [*BURST_CENTRE, BURST_ZOOM])
            page.wait_for_timeout(400)
            for p in _phases(page):
                m = re.search(r"Burst test pin (\d+)", p["title"])
                if p["running"] and m:
                    drift.append(_circ_gap(p["phase"], (p["clock"] + ids_phase[int(m.group(1)) - 1]) % 1))
            num["phase_lock_worst_drift_ms"] = round(max(drift) * PULSE_MS, 1) if drift else None
            check("pulse timing locked to clock and id, also after redraw",
                  drift and max(drift) * PULSE_MS < 40, str(num["phase_lock_worst_drift_ms"]))
            # words: hover text and preview
            t = page.evaluate("() => { const ic = [...document.querySelectorAll('.leaflet-marker-icon')]"
                              ".find(ic => ic.querySelector('.sig-pulse')); return ic ? ic.getAttribute('title') : ''; }")
            check("hover text says new", "new on the map, added 24 Oct" in t, t)
            page.evaluate("() => { const ic = [...document.querySelectorAll('.leaflet-marker-icon')]"
                          ".find(ic => ic.querySelector('.sig-pulse')); ic.click(); }")
            page.wait_for_timeout(600)
            pop = page.evaluate("() => (document.querySelector('.leaflet-popup-content') || {}).textContent || ''")
            check("preview says new", "New: added 24 Oct" in pop, pop[:200])
            key = page.evaluate("() => document.getElementById('age-key').textContent")
            check("map key explains pulse, circles and Motion", "added to the map in the last 48 hours" in key
                  and "Numbered circles never pulse" in key and "Motion" in key)
            check("halo hidden from screen readers", page.evaluate(
                "() => [...document.querySelectorAll('.sig-pulse')].every(e => { const h = e.closest('[aria-hidden]'); return !!h && h.getAttribute('aria-hidden') === 'true'; })"))
            errs_all += errs
            ctx.close()

            # ── device asks for reduced motion ──────────────────────────────
            ctx, page, errs = _open(browser, size, base + "burst/", "2026-10-24", "12:00", reduced_motion="reduce")
            page.evaluate("([la, ln, z]) => ZADAge.view(la, ln, z)",
                          [*BURST_CENTRE, BURST_ZOOM])
            page.wait_for_timeout(500)
            st = page.evaluate("() => { const b = document.getElementById('ctrl-motion');"
                               " return { c: b.getAttribute('aria-checked'), d: b.getAttribute('aria-disabled'), t: b.title }; }")
            check("reduce motion: switch off and locked", st["c"] == "false" and st["d"] == "true"
                  and "reduced motion" in st["t"], str(st))
            page.click("#ctrl-motion", force=True)
            page.wait_for_timeout(300)
            check("reduce motion: nothing moves, even after a tap",
                  not [a for a in page.evaluate(ANIMS_JS) if a["state"] == "running"]
                  and page.evaluate("() => ZADPulse.motionOn()") is False
                  and "reduced motion" in page.evaluate("() => document.getElementById('motion-status').textContent"))
            check("reduce motion: halos still there", page.evaluate(
                "() => document.querySelectorAll('.leaflet-marker-icon .sig-pulse').length") >= 50)
            errs_all += errs
            ctx.close()

            # ── an archived pin opened from its month file ──────────────────
            dec_list = map_data.read_map_list(dists / "dec" / "index.html")
            old = next(e for e in oct_list if e["signal_id"][:8] not in {d["signal_id"][:8] for d in dec_list})
            ctx, page, errs = _open(browser, size, base + "dec/?signal=" + old["signal_id"][:8], "2026-12-15", "12:00")
            page.wait_for_function("() => { const d = document.getElementById('signal-drawer'); return d && !d.hidden; }",
                                   timeout=15000)
            page.wait_for_timeout(600)
            note = page.evaluate("() => { const e = document.getElementById('sd-archive'); return e && !e.hidden ? e.textContent : ''; }")
            check("archived pin keeps its archive note", "archive. Older than 30 days" in note, note)
            check("archived pin never pulses", page.evaluate(
                "() => ZADPulse.pulsing().length === 0 && document.querySelectorAll('.sig-pulse').length <= 1"))
            errs_all += errs
            ctx.close()

            check("no page errors", not errs_all, "; ".join(errs_all[:3]))
        browser.close()
    return {"results": results, "problems": problems, "numbers": numbers}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="URL serving the oct/, dec/, upd/ and burst/ builds")
    ap.add_argument("--dists", type=Path, required=True)
    ap.add_argument("--sizes", default="computer,phone")
    ap.add_argument("--json", type=Path, help="also write the report here")
    args = ap.parse_args(argv)
    rep = run(args.base, args.dists, tuple(args.sizes.split(",")))
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    for size, res in rep["results"].items():
        print(f"[pulse] {size}: {sum(res.values())}/{len(res)} passed")
    for p in rep["problems"]:
        print("  " + p)
    print(json.dumps(rep["numbers"], indent=1))
    print("[pulse] " + ("PASS" if not rep["problems"] else "FAIL"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

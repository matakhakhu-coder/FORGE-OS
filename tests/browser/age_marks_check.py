"""Browser check for the age ring, the Updated mark and their wording (Batch 7).

It opens the map in a real browser, on a computer and on a phone, with the
date set by forge_clock, and checks:

  ring       every drawn pin's ring band matches its incident day, worked
             out here in Python, on 3, 10, 24 and 31 October; the marker's
             own element carries that band with the band's width, dash and
             fade; the ring and the Updated mark never animate
  midnight   a tab left open from 23:59 to 00:01 moves pins to the next
             band without a reload
  updated    on the upd/ build (two real pins given a made-up approved
             material update, made by make_upd below) the mark shows on the
             update's South African day and the 7 days after, is gone on day
             8, and never changes the ring; the drawer's dates line, the
             permanent "Updated [date]: [note]" line and the hover tip match
  wording    the map key in the Filter panel; the drawer of a pin with no
             update; the dec/ build, where an October pin opened from a link
             has no ring and the Batch 5 archive note
  contrast   on both sizes, every band's ring and the Updated mark against
             the map's own tiles, measured from screenshots with the pins
             hidden: the share of map pixels each clears 3:1 against

    python tests/browser/age_marks_check.py --base http://127.0.0.1:8857/ --dists /tmp/ta7

--dists is the folder --base serves, holding oct/, dec/ and upd/.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
from datetime import date, timedelta
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

# The band spec in map.html: border width, style and opacity.
BAND_STYLE = {"0": ("3px", "solid", "1"), "1": ("2px", "solid", "0.85"),
              "2": ("1px", "solid", "0.7"), "3": ("1px", "dashed", "0.6")}
RING_RGB = (0xdf, 0xe8, 0xef)

# Made-up material updates for the upd/ fixture. The second is 00:30 on
# 21 October in South Africa but still 20 October in UTC.
UPDATES = [
    {"at": "2026-10-20T09:00:00+02:00", "day": "2026-10-20",
     "note": "Test update: a second suspect was arrested.",
     "sources": [{"name": "Example News", "url": "https://example.org/update-1",
                  "domain": "example.org"}]},
    {"at": "2026-10-20T22:30:00Z", "day": "2026-10-21",
     "note": "Test update: the road reopened.",
     "sources": [{"name": "Example Daily", "url": "https://example.org/update-2",
                  "domain": "example.org"}]},
]


def expected_band(entry: dict, today: date) -> str:
    d = ma.incident_day(entry)
    if d is None:
        return "none"
    age = max((today - d).days, 0)
    if age >= 30:
        return "none"
    return "0" if age <= 2 else "1" if age <= 7 else "2" if age <= 14 else "3"


def short(day: date | str) -> str:
    d = date.fromisoformat(str(day)[:10])
    return f"{d.day} {d.strftime('%b')}"


def make_upd(oct_dist: Path, out: Path) -> list[dict]:
    """Copy the October build and give two pins an approved material update.

    Picks two pins that happened on or before 12 October, so their age band
    on 20 to 29 October is never band 0: a mark that reset the age would
    show. Changes only the page's map list and the two detail files.
    """
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(oct_dist, out)
    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    m = re.search(r"var RAW = (\[.*?\]);\n", html)
    raw = json.loads(m.group(1))
    picks = [e for e in raw if ma.incident_day(e) and ma.incident_day(e) <= date(2026, 10, 12)
             and e.get("incident_start")][:2]
    chosen = []
    for e, upd in zip(picks, UPDATES):
        e["last_material_update_at"] = upd["at"]
        detail = out / "pins" / f"{e['signal_id'][:8]}.json"
        d = json.loads(detail.read_text(encoding="utf-8"))
        d.update(last_material_update_at=upd["at"], update_note=upd["note"],
                 update_sources=upd["sources"])
        detail.write_text(json.dumps(d), encoding="utf-8")
        chosen.append({"id": e["signal_id"][:8], "title": e["title"], "entry": e, **upd})
    html = html[:m.start(1)] + json.dumps(raw) + html[m.end(1):]
    index.write_text(html, encoding="utf-8")
    return chosen


def _open(browser, size, url, day, at="12:00"):
    ctx = browser.new_context(**SIZES[size])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    forge_clock.freeze_today(page, day, at)
    page.goto(url, wait_until="load")
    page.wait_for_function("() => window.ZADAge && window.ZADLive && window.ForgePins")
    page.wait_for_timeout(300)
    return ctx, page, errors


def _marks(page) -> dict:
    return {r["id"]: r for r in page.evaluate("() => window.ZADAge.marks()")}


def _drawer(page, title, timeout=15000):
    page.wait_for_function(
        "t => { const d = document.getElementById('signal-drawer');"
        " const e = document.getElementById('sd-title');"
        " return d && !d.hidden && e && e.textContent.trim() === t; }", arg=title, timeout=timeout)
    page.wait_for_timeout(400)
    return page.evaluate(
        "() => { const g = id => { const e = document.getElementById(id);"
        " return e && !e.hidden ? e.textContent.replace(/\\s+/g, ' ').trim() : null; };"
        " return { dates: g('sd-dates'), updated: g('sd-updated'), archive: g('sd-archive'),"
        " mark: !!document.querySelector('#sd-updated .sd-updated__mark') }; }")


def _lum(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _blend(fg, alpha, bg):
    return tuple(round(alpha * f + (1 - alpha) * b) for f, b in zip(fg, bg[:3]))


CLIPS = {"computer": {"x": 420, "y": 220, "width": 520, "height": 420},
         "phone": {"x": 20, "y": 260, "width": 350, "height": 360}}


def contrast_report(browser, base: str, size: str = "computer") -> dict:
    """Screens of the map's tiles alone (pins, controls and panels hidden) at
    three views; for each mark, the share of map pixels it clears 3:1
    against, and its ratio against the median map pixel."""
    from PIL import Image
    # The real clock here: with the date frozen, Leaflet's tile fade-in never
    # advances and the tiles stay transparent. Contrast needs no set date.
    ctx = browser.new_context(**SIZES[size])
    page = ctx.new_page()
    page.goto(base + "oct/", wait_until="load")
    page.wait_for_function("() => window.ZADAge")
    dot_opacity = float(page.evaluate(
        "() => getComputedStyle(document.querySelector('.age-key .sig-dot--static')).opacity"))
    html = page.content()
    block = re.search(r"var STREAM_COLOR = \{(.*?)\};", html, re.S).group(1)
    streams = dict(re.findall(r"(\w+):\s*'#([0-9a-fA-F]{6})'", block))
    page.add_style_tag(content=".leaflet-marker-pane, .leaflet-shadow-pane, .leaflet-overlay-pane,"
                       " .leaflet-tooltip-pane, .leaflet-popup-pane { display: none !important; }")
    # The middle of the map, clear of the header, the controls and the panels.
    clip = CLIPS[size]
    views = [(-30.5, 25.0, 6), (-33.98, 18.6, 12), (-23.9, 29.45, 13)]
    pixels = []
    for lat, lng, z in views:
        page.evaluate("([a, b, z]) => window.ZADAge.view(a, b, z)", [lat, lng, z])
        page.wait_for_function("() => document.querySelectorAll('.leaflet-tile-loaded').length > 4"
                               " && !document.querySelector('.leaflet-tile-pane img.leaflet-tile:not(.leaflet-tile-loaded)')",
                               timeout=30000)
        page.wait_for_timeout(800)
        img = Image.open(io.BytesIO(page.screenshot(clip=clip, animations="disabled"))).convert("RGB")
        img = img.resize((img.width // 2, img.height // 2), Image.NEAREST)
        raw = img.tobytes()
        pixels += [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    ctx.close()
    pixels = [p for p in pixels if p != (255, 255, 255)] or [(11, 17, 23)]
    pixels.sort(key=_lum)
    median = pixels[len(pixels) // 2]
    marks = {f"ring band {b}": (RING_RGB, float(s[2])) for b, s in BAND_STYLE.items()}
    marks["Updated mark (white)"] = ((255, 255, 255), 1.0)
    for name, hexc in streams.items():
        marks[f"dot {name}"] = (tuple(int(hexc[i:i + 2], 16) for i in (0, 2, 4)), dot_opacity)
    out = {"median_map_pixel": "#%02x%02x%02x" % median, "pixels": len(pixels),
           "lightest_map_pixel_99_9": "#%02x%02x%02x" % pixels[int(len(pixels) * 0.999)],
           "lightest_map_pixel": "#%02x%02x%02x" % pixels[-1],
           "dot_opacity": dot_opacity, "marks": {}}
    for name, (fg, alpha) in marks.items():
        ok = sum(1 for p in pixels if _ratio(_blend(fg, alpha, p), p) >= 3.0)
        out["marks"][name] = {"share_at_3_to_1": round(ok / len(pixels), 4),
                              "vs_median": round(_ratio(_blend(fg, alpha, median), median), 2)}
    return out


def run(base: str, dists: Path, sizes=("computer", "phone")) -> dict:
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/") + "/"
    oct_page = map_data.read_map_list(dists / "oct" / "index.html")
    by_id = {e["signal_id"][:8]: e for e in oct_page}
    upd_page = map_data.read_map_list(dists / "upd" / "index.html")
    updated = [{"id": e["signal_id"][:8], "title": e["title"], "entry": e,
                "day": str(ma.local_day(e["last_material_update_at"]))}
               for e in upd_page if e.get("last_material_update_at")]
    results: dict[str, dict[str, bool]] = {}
    problems: list[str] = []
    numbers: dict = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for size in sizes:
            res = results.setdefault(size, {})

            def check(name, cond, detail=""):
                res[name] = bool(cond)
                if not cond:
                    problems.append(f"{size}: {name} {detail}".strip())

            all_errors: list[str] = []
            elements_ok: dict[str, bool] = {}
            # Ring bands, on four days.
            for day in ("2026-10-03", "2026-10-10", "2026-10-24", "2026-10-31"):
                ctx, page, errs = _open(browser, size, base + "oct/", day)
                today = date.fromisoformat(day)
                got = _marks(page)
                want = {i: expected_band(e, today) for i, e in by_id.items()
                        if expected_band(e, today) != "none"}
                shown = set(page.evaluate("() => window.ZADMapTime.shown()"))
                drawn = {i: r["band"] for i, r in got.items() if i in shown}
                bad = [i for i in want if drawn.get(i) != want[i]]
                counts = {b: list(want.values()).count(b) for b in "0123"}
                numbers.setdefault("bands", {})[day] = counts
                check(f"ring {day}: every drawn pin has the band of its incident day",
                      not bad and set(drawn) == set(want) and not any(r["updated"] for r in got.values()),
                      f"({len(bad)} wrong, {len(drawn)} drawn, {len(want)} expected)")
                if day in ("2026-10-10", "2026-10-24"):
                    seen = {}
                    for b in "0123":
                        sid = next((i for i, v in want.items() if v == b), None)
                        if not sid:
                            continue
                        info = page.evaluate("s => window.ZADAge.show(s)", sid)
                        seen[b] = info
                        elements_ok[b] = elements_ok.get(b, True) and bool(
                            info and info["band"] == b and info["ring"]
                            and (info["ring"]["width"], info["ring"]["style"], info["ring"]["opacity"])
                            == BAND_STYLE[b] and not info["updated"])
                        if not elements_ok[b]:
                            problems.append(f"{size}: band {b} element on {day}: {info}")
                if day == "2026-10-24":
                    anim = page.evaluate(
                        "() => [...document.querySelectorAll('.sig-ring, .sig-upd')]"
                        ".map(e => getComputedStyle(e).animationName).filter(n => n !== 'none').length")
                    check("ring: no ring or mark animates", anim == 0, f"({anim} animate)")
                    tip = seen["1"]["title"] if seen.get("1") else ""
                    check("ring: the hover tip says how long ago it happened",
                          re.search(r"\(happened \d+ days ago\)$", tip), f"({tip!r})")
                all_errors += errs
                ctx.close()

            for b in "0123":
                check(f"ring: a band {b} marker's element shows band {b}'s width, dash and fade",
                      elements_ok.get(b) is True)

            # Midnight with the tab open: 23:59 on 26 Oct to 00:01 on 27 Oct.
            ctx, page, errs = _open(browser, size, base + "oct/", "2026-10-26", "23:59")
            before = {i: r["band"] for i, r in _marks(page).items()}
            page.clock.set_fixed_time(forge_clock.sa_instant("2026-10-27", "00:01"))
            page.evaluate("() => window.ZADLive.recheck()")
            after = {i: r["band"] for i, r in _marks(page).items()}
            w26 = {i: expected_band(e, date(2026, 10, 26)) for i, e in by_id.items()}
            w27 = {i: expected_band(e, date(2026, 10, 27)) for i, e in by_id.items()}
            moved = [i for i in w26 if w26[i] != w27[i]]
            check("midnight: pins move to the next band without a reload",
                  moved and all(before.get(i, "none") == w26[i] and after.get(i, "none") == w27[i]
                                for i in by_id), f"({len(moved)} should move)")
            numbers["midnight_moved"] = len(moved)
            all_errors += errs
            ctx.close()

            # Updated mark on the upd/ build.
            for u in updated:
                uday = date.fromisoformat(u["day"])
                for offset, shown in ((0, True), (3, True), (7, True), (8, False), (12, False)):
                    day = uday + timedelta(days=offset)
                    ctx, page, errs = _open(browser, size, base + "upd/", day.isoformat())
                    got = _marks(page)
                    r = got.get(u["id"])
                    band = expected_band(u["entry"], day)
                    others = [i for i, x in got.items() if x["updated"] and i != u["id"]
                              and i not in {v["id"] for v in updated}]
                    check(f"updated {u['id']} day {offset}: mark {'shows' if shown else 'gone'}, ring unchanged",
                          r and r["updated"] == shown and r["band"] == band and not others,
                          f"({r}, want band {band})")
                    if offset in (0, 8):
                        info = page.evaluate("s => window.ZADAge.show(s)", u["id"])
                        tip_ok = info and (("updated " + short(uday)) in info["title"]) == shown
                        check(f"updated {u['id']} day {offset}: the marker element and hover tip agree",
                              info and info["updated"] == shown and tip_ok, f"({info})")
                        page.goto(base + "upd/?signal=" + u["id"], wait_until="load")
                        d = _drawer(page, u["title"])
                        e = u["entry"]
                        want_dates = "Happened %s, added %s, last updated %s" % (
                            short(ma.local_day(e["incident_start"])),
                            short(ma.local_day(e["published_at"])), short(uday))
                        note = next(x["note"] for x in UPDATES if x["day"] == u["day"])
                        check(f"updated {u['id']} day {offset}: drawer dates line",
                              d["dates"] == want_dates, f"({d['dates']!r} vs {want_dates!r})")
                        check(f"updated {u['id']} day {offset}: drawer keeps 'Updated [date]: [note]'",
                              d["updated"] and d["updated"].startswith("Updated %s: %s" % (short(uday), note))
                              and ("Updated this week" in d["updated"]) == shown and d["mark"] == shown
                              and "Source: Example" in d["updated"], f"({d['updated']!r})")
                    all_errors += errs
                    ctx.close()

            # Wording: the map key, a pin with no update, and an archived pin.
            ctx, page, errs = _open(browser, size, base + "oct/", "2026-10-24")
            page.click("#ctrl-filter")
            page.wait_for_timeout(400)
            key = page.evaluate("() => { const k = document.getElementById('age-key');"
                                " if (!k) return null; k.scrollIntoView(); const r = k.getBoundingClientRect();"
                                " return { text: k.textContent.replace(/\\s+/g, ' ').trim(),"
                                " rows: k.querySelectorAll('.age-key__row').length,"
                                " visible: r.width > 0 && r.height > 0,"
                                " rings: [...k.querySelectorAll('.sig-ring')].map(e => { const c = getComputedStyle(e);"
                                " return [c.borderTopWidth, c.borderTopStyle, c.opacity]; }),"
                                " diamonds: k.querySelectorAll('.sig-upd').length }; }")
            phrases = ["How old a pin is", "The dot's colour is its category",
                       "counted from the incident date in South African time", "last 30 days",
                       "0 to 2 days ago", "3 to 7 days ago", "8 to 14 days ago", "15 to 29 days ago",
                       "No ring: older than the 30-day live map", "month's archive",
                       "White diamond: updated in the last 7 days", "An update never changes the ring"]
            check("wording: the map key names every band, the archive and the Updated mark",
                  key and key["visible"] and key["rows"] == 6 and all(p in key["text"] for p in phrases),
                  f"(missing {[p for p in phrases if not key or p not in key['text']]})")
            check("wording: the key's rings are the map's rings",
                  key and [tuple(r) for r in key["rings"]] == [BAND_STYLE[b] for b in "0123"] + [BAND_STYLE["1"]]
                  and key["diamonds"] == 1, f"({key and key['rings']})")
            plain = next(e for e in oct_page if not e.get("last_material_update_at") and e.get("incident_start"))
            page.goto(base + "oct/?signal=" + plain["signal_id"][:8], wait_until="load")
            d = _drawer(page, plain["title"])
            want_dates = "Happened %s, added %s" % (short(ma.local_day(plain["incident_start"])),
                                                   short(ma.local_day(plain["published_at"])))
            check("wording: a pin with no update has a dates line and no Updated line",
                  d["dates"] == want_dates and d["updated"] is None and d["archive"] is None,
                  f"({d})")
            all_errors += errs
            ctx.close()

            ctx, page, errs = _open(browser, size, base + "dec/", "2026-12-15")
            page.goto(base + "dec/?signal=" + plain["signal_id"][:8], wait_until="load")
            d = _drawer(page, plain["title"])
            r = _marks(page).get(plain["signal_id"][:8])
            check("wording: an archived pin opened from a link has no ring and the archive note",
                  r and r["extra"] and r["band"] == "none" and not r["updated"]
                  and d["archive"] == "From the October 2026 archive. Older than 30 days, so it isn\u2019t on the live map."
                  and d["dates"] == want_dates, f"({r}, {d})")
            all_errors += errs
            ctx.close()
            check("no page errors", not all_errors, f"({all_errors[:3]})")

        for size in sizes:
            c = numbers.setdefault("contrast", {})[size] = contrast_report(browser, base, size)
            res = results.setdefault("contrast " + size, {})
            for name, v in c["marks"].items():
                if name.startswith("dot "):
                    continue        # category colours: reported, not Batch 7's to change
                ok = v["vs_median"] >= 3.0 and v["share_at_3_to_1"] >= 0.95
                res[f"{name} clears 3:1 against the map"] = ok
                if not ok:
                    problems.append(f"contrast {size}: {name} {v}")
        browser.close()
    return {"results": results, "problems": problems, "numbers": numbers}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="URL serving the oct/, dec/ and upd/ builds")
    ap.add_argument("--dists", type=Path, required=True)
    ap.add_argument("--sizes", default="computer,phone")
    args = ap.parse_args(argv)
    rep = run(args.base, args.dists, tuple(args.sizes.split(",")))
    for size, res in rep["results"].items():
        print(f"[age-marks] {size}: {sum(res.values())}/{len(res)} passed")
    for p in rep["problems"]:
        print("  " + p)
    print(json.dumps(rep["numbers"], indent=1))
    print("[age-marks] " + ("PASS" if not rep["problems"] else "FAIL"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

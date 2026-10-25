"""Browser check for the Earlier months view (time-aware pins, Batch 9).

The map's archive opens one whole calendar month at a time, at ?month=YYYY-MM.
This drives the real map in Chromium, on a computer and a phone, with pretend
dates (forge_clock), against the builds from time_aware_dists.py:

  oct/  today's build (24 October): October is the only month, still open
  dec/  the 15 December build: October archived, November and December empty

It checks that the month view shows exactly that month's records, that it
can't pass for the live map (banner, frame, grey tiles, hollow still pins,
words), that the way back works (button, list, browser Back), empty months,
months the archive doesn't have, bad addresses, a failed load (Try again),
keyboard and screen-reader wiring, and whether the banner runs under the
site header.

  python tests/browser/earlier_months_check.py --base http://127.0.0.1:8765/ --dists /tmp/dists
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import forge_clock  # noqa: E402
from tools import map_archive as ma  # noqa: E402
from tools import map_data  # noqa: E402

from earlier_months_js import BOX_JS, LIST_JS, SHOWN_JS, STILL_JS, VIEW_JS  # noqa: E402

SIZES = {"computer": {"viewport": {"width": 1366, "height": 900}},
         "phone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}}


def _overlap(a, b) -> bool:
    return bool(a and b) and a["left"] < b["right"] - 1 and b["left"] < a["right"] - 1 \
        and a["top"] < b["bottom"] - 1 and b["top"] < a["bottom"] - 1


def _open(browser, size, url, day, at="12:00", route_fail=False):
    ctx = browser.new_context(**SIZES[size])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    forge_clock.freeze_today(page, day, at)
    if route_fail:
        page.route("**/archive/**", lambda r: r.abort())
    page.goto(url, wait_until="load")
    page.wait_for_function("() => window.ZADMonth && window.ZADMapTime && window.ForgePins")
    page.wait_for_timeout(300)
    return ctx, page, errors


def _settle(page, state=None):
    page.wait_for_function(
        "s => { const v = window.ZADMonth.view(); return v && (s ? v.state === s : v.state !== 'loading'); }",
        arg=state, timeout=10000)
    page.wait_for_timeout(250)


def _param(page, name):
    return page.evaluate("n => new URLSearchParams(location.search).get(n)", name)


def _rows(page):
    page.wait_for_function("() => document.querySelectorAll('#mv-pop-list .mv-pop__item').length"
                           " || !document.getElementById('mv-pop-retry').hidden", timeout=10000)
    return page.evaluate(LIST_JS)


def _texts(page):
    return [r["text"] for r in _rows(page)]


def _banner(page):
    return page.evaluate("() => window.ZADMonth.banner()")


def _active(page):
    return page.evaluate("() => document.activeElement.id")


def expected_sets(dists: Path):
    records = map_data.read_map_list(dists / "oct" / "index.html")
    by_month: dict[str, set[str]] = {}
    for e in records:
        if e.get("lat") is None or e.get("lng") is None:
            continue
        by_month.setdefault(ma.month_of(e), set()).add(e["signal_id"][:8])
    dec_entries = ma.month_entries(dists / "dec")
    dec_oct = {e["signal_id"][:8] for e in dec_entries.get("2026-10", []) if e.get("lat") is not None}
    return by_month, dec_oct


def today_build(browser, size, base, october, check, notes, shots: Path):
    """Today's build on 24 October: open October from the list, then go back."""
    ctx, page, errs = _open(browser, size, base + "oct/", "2026-10-24")
    live_before = set(page.evaluate(SHOWN_JS))
    check("live map shows its 72 pins before", live_before == october, f"{len(live_before)}")
    check("no month view at first", page.evaluate(VIEW_JS) is None
          and page.evaluate("() => document.getElementById('mv-banner').hidden"))
    ctrl = page.locator("#ctrl-months")
    check("Earlier months button is visible", ctrl.is_visible())
    check("button names itself and its list", ctrl.get_attribute("aria-label") == "Earlier months"
          and ctrl.get_attribute("aria-expanded") == "false" and ctrl.get_attribute("aria-controls") == "mv-pop")
    ctrl.click()
    rows = _rows(page)
    check("list opens and says it's expanded", page.locator("#mv-pop").is_visible()
          and ctrl.get_attribute("aria-expanded") == "true")
    check("list: Live map first, marked current", bool(rows) and rows[0]["text"].startswith("Live map")
          and rows[0]["current"], str(rows[:1]))
    check("list: October 2026 so far, 72 pins, at its own address",
          len(rows) == 2 and rows[1]["text"] == "October 2026 so far 72 pins"
          and rows[1]["href"].endswith("?month=2026-10"), str(rows))
    check("list says other ranges are in Records and Area check", "Records or Area check" in page.inner_text("#mv-pop"))
    pop = page.evaluate(BOX_JS, "#mv-pop")
    vw = SIZES[size]["viewport"]["width"]
    check("list fits on screen", bool(pop) and pop["left"] >= 0 and pop["right"] <= vw, str(pop))
    page.locator("#mv-pop-list .mv-pop__item").nth(1).click()
    _settle(page, "ready")
    v = page.evaluate(VIEW_JS)
    shown = set(page.evaluate(SHOWN_JS))
    check("October view shows exactly October's records", shown == october,
          f"missing {sorted(october - shown)[:5]} extra {sorted(shown - october)[:5]}")
    check("view counts 72", bool(v) and v["total"] == len(october) == 72 and v["shown"] == 72, str(v))
    check("address is ?month=2026-10", _param(page, "month") == "2026-10")
    check("list closes after choosing", not page.locator("#mv-pop").is_visible()
          and ctrl.get_attribute("aria-expanded") == "false")
    b = _banner(page)
    check("banner names the month and says not the live map",
          "NOT THE LIVE MAP" in b.upper() and "MONTH VIEW" in b.upper() and "October 2026 so far" in b, b)
    check("banner counts the pins and says the month isn't over",
          "72 pins that happened in October 2026" in b and "isn\u2019t over yet" in b, b)
    check("Back to live map button is in the banner", page.locator("#mv-back").is_visible()
          and page.text_content("#mv-back").strip() == "Back to live map")
    check("focus moves to the month heading", _active(page) == "mv-heading")
    if size == "phone":
        check("phone: coverage note starts tucked behind About this month",
              not page.locator("#mv-note").is_visible() and page.get_attribute("#mv-more", "aria-expanded") == "false")
        page.click("#mv-more")
        page.wait_for_timeout(150)
    note = page.inner_text("#mv-note") if page.locator("#mv-note").is_visible() else ""
    check("coverage note shows (Batch 4 draft)", "not a crime rate" in note and "1 October" in note, note)
    st = page.evaluate(STILL_JS)
    check("month view: no rings, pulse, new-dots or pin animation",
          st["rings"] == 0 and st["pulses"] == 0 and st["newDots"] == 0 and st["anims"] == 0, str(st))
    check("month view: hollow pins and numbered circles",
          (st["dotBg"] or st["clusterBg"]) is not None
          and st["dotBg"] in (None, "rgba(8, 13, 18, 0.55)") and st["clusterBg"] in (None, "rgba(8, 13, 18, 0.85)"), str(st))
    check("month view: grey map in a dashed frame", "grayscale" in (st["tileFilter"] or "") and st["frame"] == "dashed", str(st))
    page.wait_for_timeout(200)
    status = page.inner_text("#mv-status")
    check("screen readers hear the month and 'not the live map'",
          "Showing October 2026: 72 pins" in status and "not the live map" in status, status)
    titles = page.evaluate("() => [...document.querySelectorAll('#map .leaflet-marker-icon[title]')].map(e => e.title)")
    check("hover text names the month view, never 'days ago'",
          all("October 2026 month view, not the live map" in t and "ago" not in t for t in titles), str(titles[:1]))
    notes.setdefault("titles_seen", {})[size] = len(titles)
    hb = page.evaluate(BOX_JS, ".site-header")
    bb = page.evaluate(BOX_JS, "#mv-banner")
    under = bool(hb and bb and bb["top"] < hb["bottom"] - 1)
    notes.setdefault("header", {})[size] = {"header_bottom": hb and round(hb["bottom"]),
                                            "banner_top": bb and round(bb["top"]), "banner_bottom": bb and round(bb["bottom"])}
    check("banner sits below the site header", not under, f"header {hb} banner {bb}")
    for other in (".md-controls", ".md-sidebar"):
        check(f"banner doesn't cover {other}", not _overlap(bb, page.evaluate(BOX_JS, other)), f"{bb} {page.evaluate(BOX_JS, other)}")
    page.screenshot(path=str(shots / f"b9-month-{size}.png"))
    page.click("#mv-back")
    page.wait_for_timeout(400)
    check("Back to live map: banner gone, address clean", page.evaluate(VIEW_JS) is None
          and page.evaluate("() => document.getElementById('mv-banner').hidden") and _param(page, "month") is None)
    check("Back to live map: grey and frame gone",
          not page.evaluate("() => document.documentElement.classList.contains('forge-month-view')"))
    check("Back to live map: same pins as before", set(page.evaluate(SHOWN_JS)) == live_before)
    check("Back to live map: focus returns to Earlier months", _active(page) == "ctrl-months")
    page.wait_for_timeout(200)
    check("Back to live map is announced", "Back to the live map" in page.inner_text("#mv-status"))
    page.go_back()
    _settle(page, "ready")
    check("browser Back returns to October", (page.evaluate(VIEW_JS) or {}).get("key") == "2026-10"
          and set(page.evaluate(SHOWN_JS)) == october)
    page.go_back()
    page.wait_for_timeout(400)
    check("browser Back again: live map", page.evaluate(VIEW_JS) is None and set(page.evaluate(SHOWN_JS)) == live_before)
    # keyboard
    page.focus("#ctrl-months")
    page.keyboard.press("Enter")
    _rows(page)
    check("keyboard: list opens with focus on its title", _active(page) == "mv-pop-title")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    check("keyboard: Escape closes it and focus returns", not page.locator("#mv-pop").is_visible()
          and _active(page) == "ctrl-months")
    page.keyboard.press("Enter")
    _rows(page)
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    focused = page.evaluate("() => document.activeElement.getAttribute('href') || document.activeElement.id")
    page.keyboard.press("Enter")
    _settle(page, "ready")
    check("keyboard: Tab to October and Enter opens it", (page.evaluate(VIEW_JS) or {}).get("key") == "2026-10", focused)
    page.keyboard.press("Tab")
    check("keyboard: next stop is Back to live map", _active(page) == "mv-back", _active(page))
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    check("keyboard: Enter on Back to live map returns", page.evaluate(VIEW_JS) is None)
    ctx.close()
    return errs


def december_build(browser, size, base, october, dec_oct, check):
    errs_all = []
    ctx, page, errs = _open(browser, size, base + "dec/", "2026-12-15")
    check("15 Dec: live map is empty", page.evaluate(SHOWN_JS) == [])
    page.click("#ctrl-months")
    got = _texts(page)
    check("15 Dec: list is Live map, December so far, November, October",
          got == ["Live map last 30 days", "December 2026 so far No pins", "November 2026 No pins", "October 2026 72 pins"], str(got))
    errs_all += errs
    ctx.close()

    ctx, page, errs = _open(browser, size, base + "dec/?month=2026-10", "2026-12-15")
    _settle(page, "ready")
    shown = set(page.evaluate(SHOWN_JS))
    check("15 Dec: ?month=2026-10 shows the 72 archived October pins", shown == dec_oct == october, f"{len(shown)}")
    b = _banner(page)
    check("15 Dec: banner says October 2026, not 'so far'", "October 2026" in b and "so far" not in b
          and "over yet" not in b, b)
    st = page.evaluate(STILL_JS)
    check("15 Dec: archived pins never pulse and have no ring",
          st["pulses"] == 0 and st["anims"] == 0 and st["rings"] == 0, str(st))
    sid = sorted(october)[0]
    page.evaluate("s => { const u = new URL(location.href); u.searchParams.set('signal', s);"
                  " history.pushState({}, '', u); dispatchEvent(new PopStateEvent('popstate')); }", sid)
    page.wait_for_timeout(700)
    note = page.evaluate("() => { const e = document.getElementById('sd-archive'); return e && !e.hidden ? e.innerText : ''; }")
    check("15 Dec: a pin opened in the month view says it's from the October 2026 archive",
          "October 2026 archive" in (note or ""), note)
    check("15 Dec: the month view stays while its drawer is open", (page.evaluate(VIEW_JS) or {}).get("key") == "2026-10")
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    check("15 Dec: closing the drawer keeps October and its address",
          (page.evaluate(VIEW_JS) or {}).get("key") == "2026-10" and _param(page, "month") == "2026-10"
          and _param(page, "signal") is None)
    errs_all += errs
    ctx.close()

    ctx, page, errs = _open(browser, size, base + f"dec/?month=2026-10&signal={sid}", "2026-12-15")
    _settle(page, "ready")
    page.wait_for_timeout(600)
    check("15 Dec: ?month=&signal= opens the month with the pin's drawer",
          bool(page.evaluate("() => !!document.querySelector('.md-shell.drawer-open')"))
          and (page.evaluate(VIEW_JS) or {}).get("key") == "2026-10")
    errs_all += errs
    ctx.close()

    ctx, page, errs = _open(browser, size, base + "dec/?month=2026-11", "2026-12-15")
    _settle(page)
    if size == "phone":
        page.click("#mv-more")
        page.wait_for_timeout(150)
    v = page.evaluate(VIEW_JS)
    b = _banner(page)
    check("empty month: November shows no pins and says so", bool(v) and v["state"] == "empty"
          and page.evaluate(SHOWN_JS) == [] and "No pins for November 2026" in b and "not that nothing happened" in b, b)
    check("empty month: the way back is there", page.locator("#mv-back").is_visible())
    errs_all += errs
    ctx.close()

    for q, want in (("2026-09", "There\u2019s no September 2026 in the archive"),
                    ("2027-03", "There\u2019s no March 2027 in the archive"),
                    ("oct", "That isn\u2019t a month the archive knows")):
        ctx, page, errs = _open(browser, size, base + f"dec/?month={q}", "2026-12-15")
        page.wait_for_timeout(700)
        msg = page.evaluate("() => window.ZADArchive.message()")
        check(f"?month={q}: live map, with a message", page.evaluate(VIEW_JS) is None and want in msg
              and _param(page, "month") is None and page.evaluate("() => document.getElementById('mv-banner').hidden"), msg)
        errs_all += errs
        ctx.close()
    return errs_all


def failures(browser, size, base, october, check):
    errs_all = []
    ctx, page, errs = _open(browser, size, base + "dec/?month=2026-10", "2026-12-15", route_fail=True)
    _settle(page, "failed")
    b = _banner(page)
    check("failed load: says so, with Try again and Back to live map",
          "Couldn\u2019t load October 2026 from the archive" in b and page.locator("#mv-retry").is_visible()
          and page.locator("#mv-back").is_visible(), b)
    check("failed load: shows no pins", page.evaluate(SHOWN_JS) == [])
    page.unroute("**/archive/**")
    page.click("#mv-retry")
    _settle(page, "ready")
    check("failed load: Try again then shows October's 72", set(page.evaluate(SHOWN_JS)) == october)
    errs_all += errs
    ctx.close()

    ctx, page, errs = _open(browser, size, base + "oct/", "2026-10-24", route_fail=True)
    page.click("#ctrl-months")
    _rows(page)
    check("failed list: says so, with Try again", "Couldn\u2019t load the earlier months" in page.inner_text("#mv-pop-status")
          and page.locator("#mv-pop-retry").is_visible())
    page.unroute("**/archive/**")
    page.click("#mv-pop-retry")
    got = _texts(page)
    check("failed list: Try again lists October", any(t.startswith("October 2026") for t in got), str(got))
    errs_all += errs
    ctx.close()
    return errs_all


def pretend_dates(browser, size, base, october, check):
    errs_all = []
    for day, want in (("2026-10-31", ["Live map last 30 days", "October 2026 so far 72 pins"]),
                      ("2026-11-23", ["Live map last 30 days", "November 2026 so far No pins", "October 2026 72 pins"])):
        ctx, page, errs = _open(browser, size, base + "oct/", day)
        page.click("#ctrl-months")
        got = _texts(page)
        check(f"{day}: list reads {want[1:]}", got == want, str(got))
        page.locator("#mv-pop-list .mv-pop__item", has_text="October 2026").click()
        _settle(page, "ready")
        check(f"{day}: October view shows all 72", set(page.evaluate(SHOWN_JS)) == october)
        errs_all += errs
        ctx.close()
    return errs_all


def run(base: str, dists: Path, sizes=("computer", "phone"), shots: Path | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/") + "/"
    shots = shots or dists
    by_month, dec_oct = expected_sets(dists)
    october = by_month.get("2026-10", set())
    results: dict[str, dict[str, bool]] = {}
    problems: list[str] = []
    notes: dict = {"months_in_records": {k: len(v) for k, v in sorted(by_month.items())}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for size in sizes:
            res = results.setdefault(size, {})

            def check(name, cond, detail="", size=size, res=res):
                print(f"  {size}: {'ok  ' if cond else 'FAIL'} {name} {'' if cond else detail}", flush=True)
                res[name] = bool(cond)
                if not cond:
                    problems.append(f"{size}: {name} {detail}".strip())

            errs = today_build(browser, size, base, october, check, notes, shots)
            errs += december_build(browser, size, base, october, dec_oct, check)
            errs += failures(browser, size, base, october, check)
            errs += pretend_dates(browser, size, base, october, check)
            check("no page errors", not errs, "; ".join(errs[:3]))
        browser.close()
    return {"results": results, "problems": problems, "notes": notes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="URL serving the oct/ and dec/ builds")
    ap.add_argument("--dists", type=Path, required=True)
    ap.add_argument("--sizes", default="computer,phone")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    rep = run(args.base, args.dists, tuple(args.sizes.split(",")))
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    for size, res in rep["results"].items():
        print(f"[months] {size}: {sum(res.values())}/{len(res)} passed")
    for p in rep["problems"]:
        print("  " + p)
    print(json.dumps(rep["notes"], indent=1))
    print("[months] " + ("PASS" if not rep["problems"] else "FAIL"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

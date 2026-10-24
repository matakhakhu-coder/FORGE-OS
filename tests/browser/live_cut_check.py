"""Browser check for Batch 5: the 30-day live cut and the shared loader.

Runs against the synth build from tests/browser/time_aware_dists.py, served
at BASE, with its folder at DIST (to work the expectations out in Python,
independently of the page). The browser's date is fixed with forge_clock.

  cut       on 15 December the map draws exactly the pins whose incident day
            (South African time) is 16 November to 15 December, including
            the midnight edge pins; the page's 1 to 15 November pins are held
            back, not drawn
  lazy      loading the map fetches nothing from archive/
  months    ForgePins.loadMonths('all') returns every pin in the month files
            once; a day range loads every month it touches
  archive   an October pin opened by ?signal= link joins the map while its
            drawer is open, says "From the October 2026 archive", and leaves
            the map when the drawer closes; a link opened twice adds it once
  midnight  a tab left open from 23:59 on 15 December to 00:01 on 16 December
            drops the 16 November pins without a reload
  failure   when the archive cannot be fetched, the page says so and Try
            again opens the pin once it can

    python tests/browser/live_cut_check.py --base http://127.0.0.1:8853/ --dist /tmp/ta5/synth
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
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


def expectations(dist: Path, today: date) -> dict:
    page = map_data.read_map_list(dist / "index.html")
    every = ma.all_entries(dist)
    def live(e):
        return (today - ma.incident_day(e)).days < 30
    on_page = {e["signal_id"][:8] for e in page}
    return {
        "live": sorted(e["signal_id"][:8] for e in page if live(e)),
        "held": sorted(e["signal_id"][:8] for e in page if not live(e)),
        "all": sorted(e["signal_id"][:8] for e in every),
        "live_anywhere": sorted(e["signal_id"][:8] for e in every if live(e)),
        "october": [e for e in every if ma.month_of(e) == "2026-10" and e["signal_id"][:8] not in on_page],
        "nov16": sorted(e["signal_id"][:8] for e in page if str(ma.incident_day(e)) == "2026-11-16"),
    }


def _open_page(browser, size, base, day, at="12:00"):
    ctx = browser.new_context(**SIZES[size])
    page = ctx.new_page()
    forge_clock.freeze_today(page, day, at)
    page.goto(base, wait_until="load")
    page.wait_for_function("() => window.ZADLive && window.ZADMapTime && window.ForgePins")
    page.wait_for_timeout(300)
    return ctx, page


def _wait_drawer(page, title, timeout=15000):
    page.wait_for_function(
        "t => { const d = document.getElementById('signal-drawer');"
        " const e = document.getElementById('sd-title');"
        " return d && !d.hidden && e && e.textContent.trim() === t; }", arg=title, timeout=timeout)


def run(base: str, dist: Path, sizes=("computer", "phone")) -> dict:
    from playwright.sync_api import sync_playwright
    base = base if base.endswith("/") else base + "/"
    exp = expectations(dist, date(2026, 12, 15))
    exp16 = expectations(dist, date(2026, 12, 16))
    results, problems = {}, []

    def check(size, name, cond, detail=""):
        results.setdefault(size, {})[name] = bool(cond)
        if not cond:
            problems.append(f"{size}: {name} failed {detail}".rstrip())

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for size in sizes:
                ctx, page = _open_page(browser, size, base, "2026-12-15")
                shown = sorted(page.evaluate("() => window.ZADMapTime.shown()"))
                check(size, "cut: drawn pins are exactly the last 30 days", shown == exp["live"],
                      f"(drawn {len(shown)}, expected {len(exp['live'])})")
                check(size, "cut: ZADLive.live() agrees",
                      sorted(page.evaluate("() => window.ZADLive.live()")) == exp["live"])
                check(size, "cut: 1 to 15 November held back on the page",
                      sorted(page.evaluate("() => window.ZADLive.archived()")) == exp["held"],
                      f"(expected {len(exp['held'])})")
                check(size, "cut: live window starts 16 November",
                      page.evaluate("() => window.ZADLive.liveFrom()") == "2026-11-16")
                check(size, "lazy: nothing fetched from archive/ on load",
                      page.evaluate("() => window.ForgePins._fetches()") == [])

                got = page.evaluate("() => window.ForgePins.loadMonths('all')"
                                    ".then(l => l.map(p => p.signal_id.slice(0, 8)))")
                check(size, "months: every pin once", sorted(got) == exp["all"] and len(got) == len(set(got)),
                      f"(got {len(got)}, unique {len(set(got))}, expected {len(exp['all'])})")
                rng = page.evaluate("() => window.ForgePins.loadMonths({from: '2026-10-28', to: '2026-11-02'})"
                                    ".then(l => l.map(p => window.ForgePins.monthOf(p)))")
                check(size, "months: a day range loads every month it touches",
                      set(rng) == {"2026-10", "2026-11"} and len(rng) == len(exp["october"]) + sum(
                          1 for e in ma.all_entries(dist) if ma.month_of(e) == "2026-11"))

                # An October pin by link, opened twice.
                octo = exp["october"][0]
                short, title = octo["signal_id"][:8], map_data.read_detail(dist, octo["signal_id"])["title"]
                for _ in range(2):
                    page.goto(base + f"?signal={short}", wait_until="load")
                    try:
                        _wait_drawer(page, title)
                        opened = True
                    except Exception:  # noqa: BLE001
                        opened = False
                check(size, "archive: ?signal= opens an October pin", opened)
                note = page.locator("#sd-archive").inner_text().strip() if opened else ""
                check(size, "archive: drawer says From the October 2026 archive",
                      note.startswith("From the October 2026 archive"), repr(note))
                check(size, "archive: its pin is drawn while the drawer is open",
                      short in page.evaluate("() => window.ZADMapTime.shown()"))
                page.evaluate("s => window.forgeOpenSignal(s)", short)   # again, same page
                page.wait_for_timeout(500)
                check(size, "archive: opening it again adds it once",
                      page.evaluate("() => window.ZADLive.fromArchive()") == [short])
                page.locator("#sd-close").click()
                page.wait_for_timeout(400)
                check(size, "archive: closing the drawer takes it off the map",
                      short not in page.evaluate("() => window.ZADMapTime.shown()")
                      and sorted(page.evaluate("() => window.ZADMapTime.shown()")) == exp["live"])
                # A live pin's drawer has no archive note.
                page.evaluate("s => window.forgeOpenSignal(s)", exp["live"][0])
                page.wait_for_timeout(500)
                check(size, "archive: a live pin shows no archive note",
                      page.locator("#sd-archive").is_hidden())
                ctx.close()

                # Midnight with the tab open.
                ctx, page = _open_page(browser, size, base, "2026-12-15", "23:59")
                before = sorted(page.evaluate("() => window.ZADMapTime.shown()"))
                page.clock.set_fixed_time(forge_clock.sa_instant("2026-12-16", "00:01"))
                page.evaluate("() => window.ZADLive.recheck()")
                after = sorted(page.evaluate("() => window.ZADMapTime.shown()"))
                check(size, "midnight: the 16 November pins leave without a reload",
                      before == exp["live"] and after == exp16["live"]
                      and set(before) - set(after) == set(exp["nov16"]) and exp["nov16"],
                      f"(before {len(before)}, after {len(after)}, 16 Nov {len(exp['nov16'])})")
                ctx.close()

                # The archive cannot be fetched, then can.
                ctx, page = _open_page(browser, size, base, "2026-12-15")
                page.route("**/archive/**", lambda r: r.fulfill(status=503, body="down"))
                octo2 = exp["october"][1]
                s2 = octo2["signal_id"][:8]
                t2 = map_data.read_detail(dist, octo2["signal_id"])["title"]
                page.evaluate("s => window.forgeOpenSignal(s)", s2)
                page.wait_for_function("() => window.ZADArchive.message().length > 0", timeout=10000)
                msg = page.evaluate("() => window.ZADArchive.message()")
                check(size, "failure: says it couldn't load the archive", "Couldn\u2019t load" in msg, repr(msg))
                page.unroute("**/archive/**")
                page.locator("#md-archive-retry").click()
                try:
                    _wait_drawer(page, t2)
                    retried = True
                except Exception:  # noqa: BLE001
                    retried = False
                check(size, "failure: Try again opens the pin", retried and not page.evaluate(
                    "() => window.ZADArchive.message()"))
                ctx.close()
        finally:
            browser.close()
    return {"results": results, "problems": problems,
            "counts": {"live_15_dec": len(exp["live"]), "held": len(exp["held"]),
                       "all": len(exp["all"]), "live_16_dec": len(exp16["live"])}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--dist", required=True)
    ap.add_argument("--sizes", default="computer,phone")
    a = ap.parse_args(argv)
    rep = run(a.base, Path(a.dist), tuple(a.sizes.split(",")))
    print(json.dumps(rep["counts"]))
    for size, res in rep["results"].items():
        print(f"[live-cut] {size}: {sum(res.values())}/{len(res)} checks passed")
    for p in rep["problems"]:
        print("  FAIL", p)
    print("[live-cut]", "PASS" if not rep["problems"] else "FAIL")
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

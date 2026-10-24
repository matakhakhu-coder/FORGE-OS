"""Browser check for Batch 6: Area check finds pins that live only in month files.

Runs against two builds from tests/browser/time_aware_dists.py: oct/ (built
for 24 October, all 72 pins on the page) as the reference, and dec/ (built
for 15 December, all 72 only in the October 2026 month file). The browser's
date is fixed with forge_clock. Area check is placed on Beacon Valley,
Mitchells Plain, 5 km.

  reference  oct/ on 24 October, All dates: the titles Area check lists, and
             no month file fetched (the page holds October in full)
  all        dec/ on 15 December, All dates: Area check loads October and
             lists exactly the reference titles, each labelled "October 2026
             archive"; those pins are drawn while the filter is on and leave
             the map when it is turned off
  label      the All button reads the same "All (since ...)" in both builds
  custom     Pick dates 1 to 31 October lists the reference titles; 16
             November to 15 December lists none and fetches no month file
  last 30    Last 30 days lists none and fetches no month file
  failure    with archive/ unreachable, the panel says the earlier months
             didn't load and offers Try again; once reachable, Try again
             lists the reference titles
  slider     with Area check off, the chart slider's map-follow and a heat-map
             cell naming three archived pins draw them; clearing them
             leaves the map empty again

    python tests/browser/area_archive_check.py --base http://127.0.0.1:8853/   # serves oct/ and dec/
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
from live_cut_check import SIZES  # noqa: E402

AREA_KEY = "zad.areaCheck.v1"
PLACE = {"lat": -34.0405, "lng": 18.6246, "label": "Beacon Valley", "radius": 5000}
MONTH_FILE = "archive/2026-10"

TITLES_JS = ("() => [...document.querySelectorAll('#ac-results .ac-res__title')]"
             ".map(e => e.textContent.trim())")
METAS_JS = ("() => [...document.querySelectorAll('#ac-results .ac-res__meta')]"
            ".map(e => e.textContent.trim())")
ARCHIVE_LINE_JS = ("() => { const a = document.getElementById('ac-archive');"
                   " return a && !a.hidden ? a.textContent.trim() : ''; }")


def _state(dates="all", frm="", to="", on=True) -> str:
    return json.dumps({"v": 1, "lat": PLACE["lat"], "lng": PLACE["lng"], "label": PLACE["label"],
                       "kind": "", "radius": PLACE["radius"], "dates": dates, "from": frm,
                       "to": to, "on": on, "remember": False})


def _open(browser, size, url, day, state=None, block_archive=False):
    ctx = browser.new_context(**SIZES[size])
    if block_archive:
        ctx.route("**/archive/**", lambda route: route.abort())
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    forge_clock.freeze_today(page, day)
    if state:
        page.add_init_script("try { sessionStorage.setItem(%s, %s); } catch (e) {}"
                             % (json.dumps(AREA_KEY), json.dumps(state)))
    page.goto(url, wait_until="load")
    page.wait_for_function("() => window.ZADArea && window.ZADMapTime && window.ForgePins")
    return ctx, page, errors


def _settle(page, want: int | None = None, timeout=15000):
    """Wait until no archive load is pending (and, given want, that many results)."""
    js = ("n => { const a = document.getElementById('ac-archive');"
          " const busy = a && !a.hidden && /Loading/.test(a.textContent);"
          " const k = document.querySelectorAll('#ac-results .ac-res__title').length;"
          " return !busy && (n === null || k === n); }")
    page.wait_for_function(js, arg=want, timeout=timeout)
    page.wait_for_timeout(200)


def _month_fetched(page) -> bool:
    return any(MONTH_FILE in f for f in page.evaluate("() => window.ForgePins._fetches()"))


def run(base: str, sizes=("computer", "phone")) -> dict:
    from playwright.sync_api import sync_playwright
    base = base if base.endswith("/") else base + "/"
    oct_url, dec_url = base + "oct/", base + "dec/"
    results, problems = {}, []

    def check(size, name, cond, detail=""):
        results.setdefault(size, {})[name] = bool(cond)
        if not cond:
            problems.append(f"{size}: {name} failed {detail}".rstrip())

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for size in sizes:
                errs: list[str] = []
                # reference: every pin on the page
                ctx, page, e = _open(browser, size, oct_url, "2026-10-24", _state())
                _settle(page)
                ref = sorted(page.evaluate(TITLES_JS))
                check(size, "reference: Area check lists reports", len(ref) >= 5, f"({len(ref)})")
                check(size, "reference: no month file fetched", not _month_fetched(page))
                page.evaluate("() => document.querySelector('.md-pill[data-panel=\"area\"]').click()")
                page.wait_for_timeout(500)
                ref_label = page.evaluate("() => document.getElementById('ac-dates-all').textContent.trim()")
                errs += e
                ctx.close()

                # all dates, December build
                ctx, page, e = _open(browser, size, dec_url, "2026-12-15", _state())
                try:
                    _settle(page, len(ref))
                except Exception:  # noqa: BLE001
                    pass
                got = sorted(page.evaluate(TITLES_JS))
                check(size, "all: archived pins listed, same as the reference", got == ref,
                      f"(listed {len(got)}, reference {len(ref)})")
                metas = page.evaluate(METAS_JS)
                check(size, "all: each labelled October 2026 archive",
                      metas and all("October 2026 archive" in m for m in metas))
                check(size, "all: October month file fetched", _month_fetched(page))
                shown = page.evaluate("() => window.ZADMapTime.shown()")
                check(size, "all: those pins drawn while the filter is on", len(shown) == len(ref),
                      f"(drawn {len(shown)})")
                page.evaluate("() => document.getElementById('ac-bar-all').click()")
                page.wait_for_timeout(300)
                check(size, "all: they leave the map when the filter is off",
                      page.evaluate("() => window.ZADMapTime.shown()") == [])
                page.evaluate("() => document.querySelector('.md-pill[data-panel=\"area\"]').click()")
                page.wait_for_timeout(800)
                label = page.evaluate("() => document.getElementById('ac-dates-all').textContent.trim()")
                check(size, "label: All reads the same in both builds", label == ref_label,
                      f"({label!r} vs {ref_label!r})")
                errs += e
                ctx.close()

                # custom ranges
                ctx, page, e = _open(browser, size, dec_url, "2026-12-15",
                                     _state("custom", "2026-10-01", "2026-10-31"))
                try:
                    _settle(page, len(ref))
                except Exception:  # noqa: BLE001
                    pass
                got = sorted(page.evaluate(TITLES_JS))
                check(size, "custom: 1 to 31 October lists the reference", got == ref,
                      f"(listed {len(got)})")
                errs += e
                ctx.close()
                ctx, page, e = _open(browser, size, dec_url, "2026-12-15",
                                     _state("custom", "2026-11-16", "2026-12-15"))
                page.wait_for_timeout(1500)
                check(size, "custom: 16 November to 15 December lists none",
                      page.evaluate(TITLES_JS) == [])
                check(size, "custom: and fetches no month file", not _month_fetched(page))
                errs += e
                ctx.close()

                # last 30 days
                ctx, page, e = _open(browser, size, dec_url, "2026-12-15", _state("30"))
                page.wait_for_timeout(1500)
                check(size, "last 30: lists none", page.evaluate(TITLES_JS) == [])
                check(size, "last 30: fetches no month file", not _month_fetched(page))
                errs += e
                ctx.close()

                # failure, then Try again
                ctx, page, e = _open(browser, size, dec_url, "2026-12-15", _state(), block_archive=True)
                try:
                    page.wait_for_function("() => !document.getElementById('ac-archive-retry').hidden",
                                           timeout=15000)
                except Exception:  # noqa: BLE001
                    pass
                line = page.evaluate(ARCHIVE_LINE_JS)
                check(size, "failure: says earlier months didn't load", "load earlier months" in line,
                      f"({line!r})")
                bar = page.evaluate("() => document.getElementById('ac-bar-text').textContent")
                check(size, "failure: the bar says so too", "didn\u2019t load" in bar, f"({bar!r})")
                ctx.unroute("**/archive/**")
                page.evaluate("() => document.querySelector('.md-pill[data-panel=\"area\"]').click()")
                page.wait_for_timeout(300)
                page.evaluate("() => document.getElementById('ac-archive-retry').click()")
                try:
                    _settle(page, len(ref))
                except Exception:  # noqa: BLE001
                    pass
                got = sorted(page.evaluate(TITLES_JS))
                check(size, "failure: Try again lists the reference", got == ref, f"(listed {len(got)})")
                check(size, "failure: message gone after Try again", page.evaluate(ARCHIVE_LINE_JS) == "")
                errs += [x for x in e if "Failed to fetch" not in x]
                ctx.close()

                # chart slider and heat-map cell, Area check off
                ctx, page, e = _open(browser, size, dec_url, "2026-12-15")
                ids = page.evaluate("() => window.ForgePins.loadIndex()"
                                    ".then(d => Object.keys(d.pins).sort().slice(0, 3))")
                for kind in ("setTime", "setCell"):
                    page.evaluate("([k, ids]) => window.ZADMapTime[k]({ ids: ids, text: 'check' })", [kind, ids])
                    try:
                        page.wait_for_function("n => window.ZADMapTime.shown().length === n", arg=len(ids),
                                               timeout=15000)
                    except Exception:  # noqa: BLE001
                        pass
                    shown = sorted(page.evaluate("() => window.ZADMapTime.shown()"))
                    check(size, f"slider: {kind} draws the archived pins it names", shown == ids,
                          f"(drawn {shown})")
                    page.evaluate("k => window.ZADMapTime[k](null)", kind)
                    page.wait_for_timeout(300)
                    check(size, f"slider: clearing {kind} empties the map",
                          page.evaluate("() => window.ZADMapTime.shown()") == [])
                errs += e
                ctx.close()

                check(size, "no page errors", not errs, f"({errs[:1]})")
        finally:
            browser.close()
    return {"results": results, "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="URL serving the oct/ and dec/ builds")
    args = ap.parse_args(argv)
    rep = run(args.base)
    for size, res in rep["results"].items():
        print(f"[area-archive] {size}: {sum(res.values())}/{len(res)} passed")
    for p in rep["problems"]:
        print("  " + p)
    print("[area-archive] " + ("PASS" if not rep["problems"] else "FAIL"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

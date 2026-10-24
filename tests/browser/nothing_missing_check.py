"""Nothing-missing check in a real browser (time-aware pins, Batch 1).

The browser half of tools/nothing_missing.py. For every approved record in
records.json it drives the built map page the way a visitor would:

  map        the pin is on the map: ZADMapTime.shown() lists it
  link       BASE?signal=<short id> opens the drawer on this pin's title
  area       on that same load, Area check (placed on the pin, 500 m, the
             record's own incident day as a custom range) lists the pin
  watchlist  starring the pin in its drawer puts it in the Watchlist, and the
             Watchlist entry opens the drawer on this pin again
  search     typing the pin's title into the site search lists it, and the
             result opens the drawer on this pin

It runs at a computer size and a phone size. Map tiles are not fetched (they
play no part in finding a pin); the Leaflet and MiniSearch scripts are.

    python -m http.server -d dist 8765 &
    python tests/browser/nothing_missing_check.py --base http://127.0.0.1:8765/
    python tests/browser/nothing_missing_check.py --base ... --today 2026-10-31

Exit code 0 means every record was reachable every way at both sizes.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from forge_clock import freeze_today            # noqa: E402
import nothing_missing as nm                    # noqa: E402

AREA_KEY = "zad.areaCheck.v1"
TILE_HOSTS = ("tile.openstreetmap.org", "basemaps.cartocdn.com", "tile.opentopomap.org")
VIEWPORTS = {
    "computer": dict(viewport={"width": 1366, "height": 800}),
    "phone": dict(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                  is_mobile=True, has_touch=True),
}


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def load_from_base(base: str) -> dict:
    """records, markers and search index, read from the served build itself."""
    base = base if base.endswith("/") else base + "/"
    return {
        "records": json.loads(_fetch(base + "records.json"))["records"],
        "markers": nm.load_markers(_fetch(base).decode("utf-8")),
        "search": json.loads(_fetch(base + "search-index.json")),
    }


def _block_tiles(context):
    def handler(route):
        if any(h in route.request.url for h in TILE_HOSTS):
            return route.abort()
        return route.continue_()
    context.route("**/*", handler)


def _drawer_title(page, timeout=15000) -> str:
    page.wait_for_function(
        "() => { const d = document.getElementById('signal-drawer');"
        " const t = document.getElementById('sd-title');"
        " return d && !d.hidden && t && t.textContent.trim().length > 0; }", timeout=timeout)
    return page.locator("#sd-title").inner_text().strip()


def _area_state(m: dict, day: str) -> str:
    return json.dumps({"v": 1, "lat": round(m["lat"], 4), "lng": round(m["lng"], 4),
                       "label": "nothing-missing check", "kind": "", "radius": 500,
                       "dates": "custom", "from": day, "to": day, "on": True, "remember": False})


def run(base: str, today: str | None = None, sizes=("computer", "phone"),
        routes=nm.ROUTES, log=print) -> dict:
    """Returns {"records": n, "sizes": {size: {route: reachable}}, "problems": [...]}."""
    from playwright.sync_api import sync_playwright

    base = base if base.endswith("/") else base + "/"
    data = load_from_base(base)
    records = data["records"]
    by_id = {str(m["signal_id"]).lower(): m for m in data["markers"]}
    problems: list[dict] = []
    out = {"records": len(records), "today": today, "sizes": {}, "problems": problems}

    def bad(size, rec, route, why):
        problems.append({"size": size, "short": rec["signal_id"][:8], "route": route, "why": why})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size in sizes:
            ok = {r: 0 for r in routes}
            ctx = browser.new_context(**VIEWPORTS[size])
            _block_tiles(ctx)
            if today:
                freeze_today(ctx, today)
            errors: list[str] = []

            # map: one load, every record drawn
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base, wait_until="load")
            page.wait_for_function("() => window.ZADMapTime && window.ZADMapTime.shown().length > 0")
            shown = set(page.evaluate("() => window.ZADMapTime.shown()"))
            if "map" in routes:
                for rec in records:
                    if rec["signal_id"][:8] in shown:
                        ok["map"] += 1
                    else:
                        bad(size, rec, "map", "not among the pins drawn on the map")

            # watchlist: star each pin in its drawer, then open each from the Watchlist
            if "watchlist" in routes:
                page.evaluate("() => { try { localStorage.removeItem('za_divergent_watchlist'); } catch (e) {} }")
                for rec in records:
                    short = rec["signal_id"][:8]
                    try:
                        page.evaluate("s => window.forgeOpenSignal(s)", short)
                        _drawer_title(page)
                        star = page.locator("#sd-star")
                        if star.get_attribute("aria-pressed") != "true":
                            star.click()
                    except Exception as e:  # noqa: BLE001
                        bad(size, rec, "watchlist", f"could not star it: {e.__class__.__name__}")
                page.keyboard.press("Escape")
                page.evaluate("() => document.querySelector('.md-pill[data-panel=\"watchlist\"]').click()")
                page.wait_for_selector("#wl-list a[data-wl-signal]", state="attached")
                listed = set(page.evaluate(
                    "() => [...document.querySelectorAll('#wl-list a[data-wl-signal]')]"
                    ".map(a => a.getAttribute('data-wl-signal'))"))
                for rec in records:
                    short = rec["signal_id"][:8]
                    if short not in listed:
                        bad(size, rec, "watchlist", "not in the Watchlist after starring")
                        continue
                    page.evaluate("s => document.querySelector('#wl-list a[data-wl-signal=\"' + s + '\"]').click()", short)
                    try:
                        title = _drawer_title(page)
                    except Exception:  # noqa: BLE001
                        bad(size, rec, "watchlist", "its Watchlist entry opened no drawer")
                        continue
                    if title != rec["title"]:
                        bad(size, rec, "watchlist", f"its Watchlist entry opened {title[:60]!r}")
                    else:
                        ok["watchlist"] += 1
                page.evaluate("() => { try { localStorage.removeItem('za_divergent_watchlist'); } catch (e) {} }")

            # search: type the title, find the pin, open it
            if "search" in routes:
                page.goto(base, wait_until="load")
                page.wait_for_function("() => typeof window.MiniSearch === 'function'", timeout=30000)
                for rec in records:
                    short = rec["signal_id"][:8]
                    sel = f'#search-results a[data-signal="{short}"]'
                    page.evaluate(
                        "q => { const i = document.getElementById('site-search'); i.value = q;"
                        " i.dispatchEvent(new Event('input', {bubbles: true})); }", rec["title"])
                    try:
                        page.wait_for_selector(sel, state="attached", timeout=10000)
                    except Exception:  # noqa: BLE001
                        bad(size, rec, "search", "its title does not bring it up in search")
                        continue
                    page.evaluate("s => document.querySelector(s).click()", sel)
                    try:
                        title = _drawer_title(page)
                    except Exception:  # noqa: BLE001
                        bad(size, rec, "search", "the search result opened no drawer")
                        continue
                    if title != rec["title"]:
                        bad(size, rec, "search", f"the search result opened {title[:60]!r}")
                    else:
                        ok["search"] += 1
            page.close()

            # link + area: one load per record
            if "link" in routes or "area" in routes:
                for rec in records:
                    short = rec["signal_id"][:8]
                    m = by_id.get(rec["signal_id"].lower())
                    day = nm.record_incident_day(rec)
                    pg = ctx.new_page()
                    pg.on("pageerror", lambda e: errors.append(str(e)))
                    if "area" in routes and m and m.get("lat") is not None and day:
                        pg.add_init_script(
                            "try { sessionStorage.setItem(%s, %s); } catch (e) {}"
                            % (json.dumps(AREA_KEY), json.dumps(_area_state(m, day))))
                    pg.goto(f"{base}?signal={short}", wait_until="load")
                    if "link" in routes:
                        try:
                            title = _drawer_title(pg)
                            if title == rec["title"]:
                                ok["link"] += 1
                            else:
                                bad(size, rec, "link", f"?signal={short} opened {title[:60]!r}")
                        except Exception:  # noqa: BLE001
                            bad(size, rec, "link", f"?signal={short} opened no drawer")
                    if "area" in routes:
                        titles = pg.evaluate(
                            "() => [...document.querySelectorAll('#ac-results .ac-res__title')]"
                            ".map(e => e.textContent.trim())")
                        if rec["title"] in titles:
                            ok["area"] += 1
                        else:
                            bad(size, rec, "area", f"Area check on the pin for {day} lists "
                                                   f"{len(titles)} reports, not this one")
                    pg.close()

            if errors:
                problems.append({"size": size, "short": "-", "route": "page",
                                 "why": f"{len(errors)} page errors, first: {errors[0][:200]}"})
            out["sizes"][size] = ok
            if log:
                log(f"[nothing-missing:browser] {size}: " +
                    ", ".join(f"{r} {ok[r]}/{len(records)}" for r in routes))
            ctx.close()
        browser.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Nothing-missing check in a real browser.")
    ap.add_argument("--base", required=True, help="URL of a served dist/, e.g. http://127.0.0.1:8765/")
    ap.add_argument("--today", default=None, help="pretend today is YYYY-MM-DD (SAST noon)")
    ap.add_argument("--sizes", default="computer,phone")
    args = ap.parse_args(argv)
    rep = run(args.base, args.today, tuple(args.sizes.split(",")))
    for pr in rep["problems"]:
        print(f"  MISSING [{pr['size']}] {pr['short']} {pr['route']}: {pr['why']}")
    print("[nothing-missing:browser] " + ("PASS" if not rep["problems"] else f"FAIL ({len(rep['problems'])})"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

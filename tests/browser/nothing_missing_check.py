"""Nothing-missing check in a real browser (time-aware pins, Batch 1).

The browser half of tools/nothing_missing.py. For every approved record in
records.json it drives the built map page the way a visitor would:

  map        the pin is on the map: ZADMapTime.shown() lists it
  link       BASE?signal=<short id> opens the drawer on this pin's title,
             and (from Batch 3) its details arrive: the drawer fetches
             pins/<short id>.json and reaches data-detail="ready"
  area       on a load of its own, with no ?signal= link (from Batch 6),
             Area check placed on the pin (500 m, the record's own incident
             day as a custom range) lists the pin. For an archived pin that
             means Area check loaded its month by date: no link fetched it
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

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
        "archive": _archive_entries(base),
    }


def _archive_entries(base: str) -> list[dict]:
    """Batch 4+: every pin in the month files (map fields). [] before Batch 4."""
    try:
        index = json.loads(_fetch(base + "archive/index.json"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for row in index.get("months") or []:
        doc = json.loads(_fetch(base + row["file"]))
        out += [f["properties"] for f in doc.get("features") or []]
    return out


def _sa_today(today: str | None) -> date:
    if today:
        return date.fromisoformat(today)
    return datetime.now(timezone(timedelta(hours=2))).date()


def _live_by_date(entry: dict, today: date) -> bool:
    """Batch 5's live cut, worked out here independently of the page: the
    incident day (South African time) is today or one of the 29 days before."""
    from tools import map_archive as ma
    d = ma.incident_day(entry)
    return d is None or (today - d).days < 30


def _block_tiles(context):
    def handler(route):
        if any(h in route.request.url for h in TILE_HOSTS):
            return route.abort()
        return route.continue_()
    context.route("**/*", handler)


def _drawer_title(page, timeout=15000, expect: str | None = None) -> str:
    """The open drawer's title. With expect, wait for that title first (a pin
    from a month file opens a moment later, over the drawer already open)."""
    if expect is not None:
        try:
            page.wait_for_function(
                "t => { const d = document.getElementById('signal-drawer');"
                " const e = document.getElementById('sd-title');"
                " return d && !d.hidden && e && e.textContent.trim() === t; }", arg=expect.strip(),
                timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
    page.wait_for_function(
        "() => { const d = document.getElementById('signal-drawer');"
        " const t = document.getElementById('sd-title');"
        " return d && !d.hidden && t && t.textContent.trim().length > 0; }", timeout=timeout)
    return page.locator("#sd-title").inner_text().strip()


def _details_ready(page, timeout=15000) -> str:
    """Batch 3: '' once the open drawer's details have arrived, else why not.
    A build from before Batch 3 has no ZADPinDetails and fills at once."""
    if not page.evaluate("() => !!window.ZADPinDetails"):
        return ""
    try:
        page.wait_for_function(
            "() => ['ready', 'error'].includes(window.ZADPinDetails.state())", timeout=timeout)
    except Exception:  # noqa: BLE001
        return "its details never arrived"
    state = page.evaluate("() => window.ZADPinDetails.state()")
    if state != "ready":
        return f"its details failed to load (drawer says {state!r})"
    return ""


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
    by_id = {str(m["signal_id"]).lower(): m for m in data.get("archive") or []}
    by_id.update({str(m["signal_id"]).lower(): m for m in data["markers"]})
    on_page = {str(m["signal_id"]).lower() for m in data["markers"]}
    sa_day = _sa_today(today)
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
            live_cut = page.evaluate("() => !!window.ForgePins")
            if live_cut:
                page.wait_for_function("() => window.ZADMapTime && window.ZADLive")
                page.wait_for_timeout(300)
            else:
                page.wait_for_function("() => window.ZADMapTime && window.ZADMapTime.shown().length > 0")
            shown = set(page.evaluate("() => window.ZADMapTime.shown()"))
            if "map" in routes and live_cut:
                # Batch 5: live pins are drawn; older pins are not, and are
                # either held back on the page or in their month file.
                held = set(page.evaluate("() => window.ZADLive.archived()"))
                in_months = set(page.evaluate(
                    "() => window.ForgePins.loadMonths('all').then(l => l.map(p => p.signal_id.slice(0, 8)))"))
                for rec in records:
                    short = rec["signal_id"][:8]
                    entry = by_id.get(rec["signal_id"].lower()) or rec
                    if _live_by_date(entry, sa_day):
                        if short in shown:
                            ok["map"] += 1
                        else:
                            bad(size, rec, "map", f"live on {sa_day} but not drawn on the map")
                    elif short in shown:
                        bad(size, rec, "map", f"older than 30 days on {sa_day} but drawn on the live map")
                    elif short in held or (rec["signal_id"].lower() not in on_page and short in in_months):
                        ok["map"] += 1
                    else:
                        bad(size, rec, "map", "off the live map and in no month file the loader reads")
            elif "map" in routes:
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
                        if _drawer_title(page, expect=rec["title"]) != rec["title"]:
                            raise RuntimeError("drawer opened another pin")
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
                        title = _drawer_title(page, expect=rec["title"])
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
                        title = _drawer_title(page, expect=rec["title"])
                    except Exception:  # noqa: BLE001
                        bad(size, rec, "search", "the search result opened no drawer")
                        continue
                    if title != rec["title"]:
                        bad(size, rec, "search", f"the search result opened {title[:60]!r}")
                    else:
                        ok["search"] += 1
            page.close()

            # link: one load per record
            if "link" in routes:
                for rec in records:
                    short = rec["signal_id"][:8]
                    pg = ctx.new_page()
                    pg.on("pageerror", lambda e: errors.append(str(e)))
                    pg.goto(f"{base}?signal={short}", wait_until="load")
                    if "link" in routes:
                        try:
                            title = _drawer_title(pg, expect=rec["title"])
                            why = _details_ready(pg) if title == rec["title"] else ""
                            if title == rec["title"] and not why:
                                ok["link"] += 1
                            elif why:
                                bad(size, rec, "link", f"?signal={short} opened it, but {why}")
                            else:
                                bad(size, rec, "link", f"?signal={short} opened {title[:60]!r}")
                        except Exception:  # noqa: BLE001
                            bad(size, rec, "link", f"?signal={short} opened no drawer")
                    pg.close()

            # area: one load per record, no link, so an archived pin must come
            # from Area check loading its month by date (Batch 6)
            if "area" in routes:
                for rec in records:
                    m = by_id.get(rec["signal_id"].lower())
                    day = nm.record_incident_day(rec)
                    if not (m and m.get("lat") is not None and day):
                        bad(size, rec, "area", "no position or incident day to place Area check on")
                        continue
                    pg = ctx.new_page()
                    pg.on("pageerror", lambda e: errors.append(str(e)))
                    pg.add_init_script(
                        "try { sessionStorage.setItem(%s, %s); } catch (e) {}"
                        % (json.dumps(AREA_KEY), json.dumps(_area_state(m, day))))
                    pg.goto(base, wait_until="load")
                    try:
                        pg.wait_for_function(
                            "t => [...document.querySelectorAll('#ac-results .ac-res__title')]"
                            ".some(e => e.textContent.trim() === t)", arg=rec["title"], timeout=15000)
                        ok["area"] += 1
                    except Exception:  # noqa: BLE001
                        titles = pg.evaluate(
                            "() => [...document.querySelectorAll('#ac-results .ac-res__title')]"
                            ".map(e => e.textContent.trim())")
                        status = pg.evaluate("() => { const a = document.getElementById('ac-archive');"
                                             " return a && !a.hidden ? a.textContent.trim() : ''; }")
                        bad(size, rec, "area", f"Area check on the pin for {day} lists "
                                               f"{len(titles)} reports, not this one"
                                               + (f" ({status})" if status else ""))
                    pg.close()

            if "month" in routes:
                # Batch 9 (opt-in route): every record is drawn in the
                # Earlier months view of its own month, at ?month=YYYY-MM.
                months: dict[str, list] = {}
                for rec in records:
                    m = by_id.get(rec["signal_id"].lower())
                    day = nm.record_incident_day(rec)
                    if not (m and m.get("lat") is not None and day):
                        bad(size, rec, "month", "no position or incident day for a month view")
                        continue
                    months.setdefault(str(day)[:7], []).append(rec)
                for key, recs in sorted(months.items()):
                    pg = ctx.new_page()
                    pg.on("pageerror", lambda e: errors.append(str(e)))
                    pg.goto(base + "?month=" + key, wait_until="load")
                    try:
                        pg.wait_for_function("() => window.ZADMonth && window.ZADMonth.view()"
                                             " && window.ZADMonth.view().state !== 'loading'", timeout=15000)
                        pg.wait_for_timeout(300)
                        in_view = set(pg.evaluate("() => window.ZADMapTime.shown()"))
                        state = pg.evaluate("() => window.ZADMonth.view().state")
                    except Exception:  # noqa: BLE001
                        in_view, state = set(), "no month view"
                    for rec in recs:
                        if rec["signal_id"][:8] in in_view:
                            ok["month"] += 1
                        else:
                            bad(size, rec, "month", f"not drawn in the {key} month view ({state})")
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
    ap.add_argument("--routes", default=",".join(nm.ROUTES))
    args = ap.parse_args(argv)
    rep = run(args.base, args.today, tuple(args.sizes.split(",")), routes=tuple(args.routes.split(",")))
    for pr in rep["problems"]:
        print(f"  MISSING [{pr['size']}] {pr['short']} {pr['route']}: {pr['why']}")
    print("[nothing-missing:browser] " + ("PASS" if not rep["problems"] else f"FAIL ({len(rep['problems'])})"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

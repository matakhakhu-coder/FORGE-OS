"""Drawer parity: does every pin's drawer show exactly what it showed before?

Time-aware pins, Batch 3 moved each pin's details out of the home page into
pins/<short id>.json, fetched when the drawer opens. This compares two served
builds, pin by pin, in a real browser:

  drawer     ?signal=<short id> on each build; once the drawer is filled (on
             the newer build: its details have arrived), every drawer part
             that shows, its text, and every link in it must match
  searches   a fixed list of searches lists the same pins, in the same order

Both builds run with today frozen to the same day (forge_clock.freeze_today),
so any "days ago" wording matches.

    python tests/browser/drawer_parity_check.py \
        --before http://127.0.0.1:8764/ --after http://127.0.0.1:8765/

Exit 0 when everything matches. Batch 11 reruns it against main.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_clock import freeze_today  # noqa: E402

VIEWPORTS = {
    "computer": {"viewport": {"width": 1366, "height": 900}},
    "phone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True,
              "device_scale_factor": 3},
}

SEARCHES = ("Musina", "Polokwane", "Mitchells Plain", "Athlone", "Gqeberha", "Cape Town",
            "Limpopo", "shooting", "murder", "water", "outage", "arrest", "cigarettes",
            "police", "school", "taxi", "Hanover Park", "Khayelitsha", "Durban", "Johannesburg")

# What the drawer shows: each visible part by id, its text, and the links in it.
# The Batch 3 loading line (#sd-detail-status) is left out; it is hidden once
# the details are in.
SNAPSHOT_JS = """() => {
  const d = document.getElementById('signal-drawer');
  const shown = el => !el.closest('[hidden]');
  const parts = Array.from(d.querySelectorAll('[id^="sd-"]'))
    .filter(el => el.id !== 'sd-detail-status' && !el.closest('#sd-detail-status'))
    .map(el => [el.id, shown(el), shown(el) ? el.innerText.trim() : '']);
  const links = Array.from(d.querySelectorAll('a'))
    .filter(a => shown(a))
    .map(a => [a.getAttribute('href') || '', a.innerText.trim()]);
  return { parts, links };
}"""

READY_JS = """() => {
  const d = document.getElementById('signal-drawer');
  const t = document.getElementById('sd-title');
  if (!d || d.hidden || !t || !t.textContent.trim()) return false;
  return !window.ZADPinDetails || window.ZADPinDetails.state() === 'ready';
}"""


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (local test server)
        return r.read()


def _block_tiles(ctx) -> None:
    ctx.route("**/*", lambda r: r.abort() if "tile.openstreetmap.org" in r.request.url
              or "basemaps.cartocdn.com" in r.request.url else r.continue_())


def _drawer(page, base: str, short: str) -> dict:
    page.goto(base.rstrip("/") + "/?signal=" + short, wait_until="load")
    page.wait_for_function(READY_JS, timeout=20000)
    return page.evaluate(SNAPSHOT_JS)


def _search(page, q: str) -> list[str]:
    page.evaluate(
        "q => { const i = document.getElementById('site-search'); i.value = q;"
        " i.dispatchEvent(new Event('input', {bubbles: true})); }", q)
    page.wait_for_timeout(700)
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#search-results a[data-signal]'))"
        ".map(a => a.getAttribute('data-signal'))")


def _diff(a: dict, b: dict) -> str:
    pa, pb = {p[0]: p for p in a["parts"]}, {p[0]: p for p in b["parts"]}
    for k in sorted(set(pa) | set(pb)):
        if pa.get(k) != pb.get(k):
            return f"{k}: before {pa.get(k)!r:.160} / after {pb.get(k)!r:.160}"
    if a["links"] != b["links"]:
        return f"links: before {a['links']!r:.200} / after {b['links']!r:.200}"
    return ""


def run(before: str, after: str, sizes=("computer", "phone"), today: str = "2026-10-24") -> dict:
    from playwright.sync_api import sync_playwright

    before, after = before.rstrip("/") + "/", after.rstrip("/") + "/"
    records = json.loads(_fetch(after + "records.json"))["records"]
    rep = {"pins": len(records), "sizes": {}, "problems": []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size in sizes:
            same = 0
            ctxs = {}
            for name in ("before", "after"):
                ctx = browser.new_context(**VIEWPORTS[size])
                _block_tiles(ctx)
                freeze_today(ctx, today)
                ctxs[name] = (ctx, ctx.new_page())
            pb, pa = ctxs["before"][1], ctxs["after"][1]
            for rec in records:
                short = rec["signal_id"][:8]
                try:
                    a, b = _drawer(pb, before, short), _drawer(pa, after, short)
                except Exception as exc:  # noqa: BLE001
                    rep["problems"].append({"size": size, "what": f"drawer {short}",
                                            "why": f"did not fill: {exc}"[:200]})
                    continue
                why = _diff(a, b)
                if why:
                    rep["problems"].append({"size": size, "what": f"drawer {short}", "why": why})
                else:
                    same += 1
            # searches
            for pg, base in ((pb, before), (pa, after)):
                pg.goto(base, wait_until="load")
                pg.wait_for_function("() => typeof window.MiniSearch === 'function'", timeout=30000)
            same_q = 0
            for q in SEARCHES:
                rb, ra = _search(pb, q), _search(pa, q)
                if rb == ra:
                    same_q += 1
                else:
                    rep["problems"].append({"size": size, "what": f"search {q!r}",
                                            "why": f"before {rb} / after {ra}"})
            rep["sizes"][size] = {"drawers_same": same, "searches_same": same_q,
                                  "searches": len(SEARCHES)}
            for ctx, _ in ctxs.values():
                ctx.close()
        browser.close()
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Drawer and search parity between two served builds.")
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--sizes", default="computer,phone")
    ap.add_argument("--today", default="2026-10-24")
    args = ap.parse_args(argv)
    rep = run(args.before, args.after, tuple(args.sizes.split(",")), args.today)
    for pr in rep["problems"]:
        print(f"  DIFFERS [{pr['size']}] {pr['what']}: {pr['why']}")
    for size, s in rep["sizes"].items():
        print(f"[drawer-parity] {size}: drawers {s['drawers_same']}/{rep['pins']} the same, "
              f"searches {s['searches_same']}/{s['searches']} the same")
    print("[drawer-parity] " + ("PASS" if not rep["problems"] else f"FAIL ({len(rep['problems'])})"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

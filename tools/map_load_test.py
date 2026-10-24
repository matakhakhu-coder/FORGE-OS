"""Map load test (time-aware pins, Batch 1).

How does the map page cope with more pins? This builds copies of today's map
page with 200, 1,000, 5,000 and 10,000 made-up pins and measures, in Chromium,
at a computer size and a phone size:

  page size       bytes of index.html, as sent (gzip) and unpacked
  usable          ms from navigation until pins are drawn and the page has
                  stopped blocking (no long task for 500 ms)
  blocking        total blocking time: the part of each long task over 50 ms
  panning         a 1.3 s drag across the map and back, then 1 s settle, at
                  the opening view and again over the Cape Flats (zoom 12):
                  frame times (median, 95th percentile, worst), the share of
                  frames slower than 50 ms, and long tasks while panning
  heap            JavaScript memory in use once usable

Today's real page (72 pins) is measured alongside as a reference.

How the copies are made: the made-up pins are today's real pins cycled and
copied with new ids and positions moved up to about 25 km, so each one carries
the same fields and text as a real pin. They replace the map's marker list
(var RAW) and the chart table rows (#gp-chart-data). Everything else in the
page stays as built today. The browser fetches Leaflet and MiniSearch once and
then reuses them; map tiles are not fetched, so tile servers do not colour
the numbers.

  computer   1366 x 800, no CPU or network slowdown
  phone      390 x 844, CPU slowed 4x, network held to 1.6 Mbps down with
             150 ms latency (the "slow 4G" setting Lighthouse uses), page
             sent gzipped as Vercel sends it

    python tools/map_load_test.py                      # all sizes, 3 runs each
    python tools/map_load_test.py --sizes 200,1000 --runs 1
    python tools/map_load_test.py --out docs/perf/map-load-baseline-2026-10-24.json

The numbers are for comparing builds on the same machine, not absolute truth.
"""
from __future__ import annotations

import argparse
import gzip
import http.server
import json
import os
import platform
import random
import re
import shutil
import socketserver
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DEFAULT_SIZES = (200, 1000, 5000, 10000)
SA_TZ = timezone(timedelta(hours=2))
ZA_BOX = {"lat": (-34.9, -22.1), "lng": (16.4, 32.9)}
JITTER_DEG = 0.25
RAW_RE = re.compile(r"var RAW = (\[.*?\]);\n", re.S)
CHART_RE = re.compile(r'(<script type="application/json" id="gp-chart-data">)(.*?)(</script>)', re.S)
TILE_HOSTS = ("tile.openstreetmap.org", "basemaps.cartocdn.com", "tile.opentopomap.org")

PROFILES = {
    "computer": {"context": dict(viewport={"width": 1366, "height": 800}),
                 "cpu_slowdown": 1, "network": None},
    "phone": {"context": dict(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                              is_mobile=True, has_touch=True),
              "cpu_slowdown": 4,
              "network": {"offline": False, "latency": 150,
                          "downloadThroughput": 1.6 * 1024 * 1024 / 8,
                          "uploadThroughput": 750 * 1024 / 8}},
}

# Watches the page from its first byte: when pins first appear, and long tasks.
INIT_JS = r"""
(() => {
  const W = window.__forgeLoad = { longTasks: [], markersAt: null };
  try {
    new PerformanceObserver(l => { for (const e of l.getEntries()) W.longTasks.push([e.startTime, e.duration]); })
      .observe({ type: 'longtask', buffered: true });
  } catch (e) { W.noLongTask = true; }
  const seen = () => {
    if (W.markersAt == null && document.querySelector('.leaflet-marker-icon, .marker-cluster')) {
      W.markersAt = performance.now(); mo.disconnect();
    }
  };
  const mo = new MutationObserver(seen);
  mo.observe(document, { childList: true, subtree: true });
  // Test-only: keep a handle on the Leaflet map so the pan test can move to a
  // city. The page itself is not changed.
  let _L;
  Object.defineProperty(window, 'L', { configurable: true, enumerable: true,
    get() { return _L; },
    set(v) { _L = v; try { v.Map.addInitHook(function () { window.__forgeMap = this; }); } catch (e) {} } });
})();
"""


# ── Made-up pins ──────────────────────────────────────────────────────────────

def load_raw(html: str) -> list[dict]:
    m = RAW_RE.search(html)
    if not m:
        raise ValueError("map page has no 'var RAW = [...]'")
    return json.loads(m.group(1))


def synthetic_markers(real: list[dict], n: int, seed: int = 20261024) -> list[dict]:
    """n made-up pins: real pins cycled, new unique ids, positions nudged."""
    rng = random.Random(seed + n)
    geo = [m for m in real if m.get("lat") is not None and m.get("lng") is not None]
    if not geo:
        raise ValueError("no real pins with a position to copy")
    out, shorts = [], set()
    i = 0
    while len(out) < n:
        src = geo[len(out) % len(geo)]
        sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"forge-map-loadtest/{n}/{i}"))
        i += 1
        if sid[:8] in shorts:          # keep ?signal= short ids unique, as the real map needs
            continue
        shorts.add(sid[:8])
        m = json.loads(json.dumps(src))
        m["signal_id"] = sid
        if m.get("slug"):
            m["slug"] = f"{m['slug']}-lt{len(out)}"
        lat = src["lat"] + rng.uniform(-JITTER_DEG, JITTER_DEG)
        lng = src["lng"] + rng.uniform(-JITTER_DEG, JITTER_DEG)
        m["lat"] = round(min(max(lat, ZA_BOX["lat"][0]), ZA_BOX["lat"][1]), 5)
        m["lng"] = round(min(max(lng, ZA_BOX["lng"][0]), ZA_BOX["lng"][1]), 5)
        out.append(m)
    return out


def synthetic_chart(table: dict, markers: list[dict]) -> dict:
    """The chart table with one row per made-up pin (rows copied from real ones)."""
    t = json.loads(json.dumps(table))
    rows = table.get("rows") or []
    real_n = max(1, len(rows))
    n = len(markers)
    if rows:
        idcol = (table.get("columns") or ["id"]).index("id")
        new = []
        for k, m in enumerate(markers):
            r = list(rows[k % len(rows)])
            r[idcol] = m["signal_id"][:8]
            new.append(r)
        t["rows"] = new
    for s in t.get("steps") or []:
        s["total"] = round(s["total"] * n / real_n)
    t["total"] = n
    return t


def synthetic_page(html: str, n: int, seed: int = 20261024) -> str:
    """Today's map page with n made-up pins in place of the real ones."""
    real = load_raw(html)
    markers = synthetic_markers(real, n, seed)
    m = RAW_RE.search(html)
    page = html[:m.start(1)] + json.dumps(markers, ensure_ascii=False, separators=(",", ":")) + html[m.end(1):]
    c = CHART_RE.search(page)
    if c:
        chart = synthetic_chart(json.loads(c.group(2)), markers)
        body = json.dumps(chart, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        page = page[:c.start(2)] + body + page[c.end(2):]
    return page


def page_sizes(html: str) -> dict:
    raw = html.encode("utf-8")
    m = RAW_RE.search(html)
    return {"html_bytes": len(raw), "html_gzip_bytes": len(gzip.compress(raw, 6)),
            "marker_json_bytes": len(m.group(1).encode("utf-8")) if m else None}


# ── A local server that gzips HTML the way Vercel does ───────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    gz_cache: dict = {}

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        path = self.translate_path(self.path.split("?", 1)[0])
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if path.endswith(".html") and "gzip" in (self.headers.get("Accept-Encoding") or "") and os.path.exists(path):
            body = self.gz_cache.get(path)
            if body is None:
                body = self.gz_cache[path] = gzip.compress(Path(path).read_bytes(), 6)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(directory: Path):
    handler = lambda *a, **k: _Handler(*a, directory=str(directory), **k)  # noqa: E731
    srv = _Server(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


# ── Measuring ────────────────────────────────────────────────────────────────

_CDN_CACHE: dict = {}


def _route(route):
    url = route.request.url
    if any(h in url for h in TILE_HOSTS):
        return route.abort()
    if url.startswith("http://127.0.0.1") or route.request.method != "GET":
        return route.continue_()
    hit = _CDN_CACHE.get(url)
    if hit is None:
        try:
            resp = route.fetch()
            hit = _CDN_CACHE[url] = (resp.status, {k: v for k, v in resp.headers.items()
                                                   if k.lower() not in ("content-encoding", "content-length")},
                                     resp.body())
        except Exception:  # noqa: BLE001
            return route.abort()
    status, headers, body = hit
    return route.fulfill(status=status, headers=headers, body=body)


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


CITY_VIEW = (-33.98, 18.56, 12)     # Cape Flats, where real pins are densest


def _wait_quiet(page, timeout_s: float):
    """Wait until no long task has run for 500 ms."""
    deadline = time.time() + timeout_s
    while True:
        st = page.evaluate("""() => { const W = window.__forgeLoad, now = performance.now();
            const ends = W.longTasks.map(t => t[0] + t[1]);
            return { now, lastEnd: ends.length ? Math.max(...ends) : 0 }; }""")
        if st["now"] - st["lastEnd"] >= 500:
            return
        if time.time() > deadline:
            raise TimeoutError("page never went quiet")
        time.sleep(0.1)


def _pan(page, key: str, timeout_s: float) -> dict:
    """Drag across the map and back (about 1.3 s), then 1 s to settle; frame stats."""
    box = page.locator("#map").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    step = max(6, box["width"] / 80)
    page.evaluate("""() => { const P = window.__forgePan = { t: [], lt0: window.__forgeLoad.longTasks.length };
        const m = window.__forgeMap; P.c0 = m ? m.latLngToContainerPoint(m.getCenter()) : null;
        P.p0 = m ? m.getPixelOrigin() : null;
        const f = ts => { P.t.push(ts); if (!P.stop) requestAnimationFrame(f); }; requestAnimationFrame(f); }""")
    page.mouse.move(cx, cy)
    page.mouse.down()
    x = cx
    moved_px = None
    for k in range(80):
        x += step if k < 40 else -step
        page.mouse.move(x, cy + (k % 2))
        if k == 39:
            moved_px = page.evaluate("""() => { const m = window.__forgeMap; if (!m) return null;
                const pos = m._getMapPanePos ? m._getMapPanePos() : null; return pos ? Math.abs(pos.x) : null; }""")
        time.sleep(0.016)
    page.mouse.up()
    time.sleep(1.0)
    pan = page.evaluate("""() => { const P = window.__forgePan; P.stop = true;
        const d = []; for (let i = 1; i < P.t.length; i++) d.push(P.t[i] - P.t[i - 1]);
        const lt = window.__forgeLoad.longTasks.slice(P.lt0);
        return { gaps: d, span: P.t.length ? P.t[P.t.length - 1] - P.t[0] : 0,
                 long_tasks: lt.length, longest_task: lt.reduce((a, t) => Math.max(a, t[1]), 0) }; }""")
    g = pan["gaps"]
    return {
        f"{key}_moved_px": round(moved_px, 1) if isinstance(moved_px, (int, float)) else None,
        f"{key}_frames": len(g),
        f"{key}_fps": round(len(g) / (pan["span"] / 1000), 1) if pan["span"] else None,
        f"{key}_frame_median_ms": round(statistics.median(g), 1) if g else None,
        f"{key}_frame_p95_ms": round(_pct(g, 0.95), 1) if g else None,
        f"{key}_frame_worst_ms": round(max(g), 1) if g else None,
        f"{key}_slow_frame_share": round(sum(1 for v in g if v > 50) / len(g), 3) if g else None,
        f"{key}_long_tasks": pan["long_tasks"],
        f"{key}_longest_task_ms": round(pan["longest_task"], 1),
    }


IDLE_VIEW = (-33.98, 18.56, 14)     # the same spot, close enough that pins come apart
IDLE_MS = 3000


def _idle(page, cdp, timeout_s: float) -> dict:
    """The cost of sitting still (Batch 8): with nothing touched for 3 s at
    street level, how much main-thread work the page does (CDP TaskDuration,
    under the profile's slowed processor), how smooth its frames are, and how
    many pin animations are running. Without animation this is near zero."""
    page.evaluate("""(c) => { const m = window.__forgeMap; if (m) m.setView([c[0], c[1]], c[2], { animate: false }); }""",
                  list(IDLE_VIEW))
    _wait_quiet(page, timeout_s)
    page.wait_for_timeout(500)
    cdp.send("Performance.enable")

    def task_s():
        return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}.get("TaskDuration", 0.0)
    t0 = task_s()
    gaps = page.evaluate("""ms => new Promise(res => { const g = []; let last = null; const t0 = performance.now();
        function f(t) { if (last !== null) g.push(t - last); last = t;
            if (t - t0 < ms) requestAnimationFrame(f); else res(g); } requestAnimationFrame(f); })""", IDLE_MS)
    t1 = task_s()
    anims = page.evaluate("""() => document.getAnimations().filter(a => a.playState === 'running' && a.effect
        && a.effect.target && a.effect.target.closest && a.effect.target.closest('.leaflet-marker-icon')).length""")
    icons = page.evaluate("() => document.querySelectorAll('.leaflet-marker-icon').length")
    return {"idle_cpu_ms_per_s": round((t1 - t0) * 1000 / (IDLE_MS / 1000), 1),
            "idle_frame_p95_ms": round(_pct(gaps, 0.95), 1) if gaps else None,
            "idle_frame_max_ms": round(max(gaps), 1) if gaps else None,
            "idle_long_frames": sum(1 for g in gaps if g > 50),
            "idle_running_pin_animations": anims, "idle_marker_icons": icons}


def measure_once(browser, url: str, profile: str, timeout_s: float = 240) -> dict:
    prof = PROFILES[profile]
    ctx = browser.new_context(**prof["context"])
    ctx.route("**/*", _route)
    ctx.add_init_script(INIT_JS)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    cdp = ctx.new_cdp_session(page)
    if prof["cpu_slowdown"] > 1:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": prof["cpu_slowdown"]})
    if prof["network"]:
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", prof["network"])
    res: dict = {"profile": profile}
    try:
        page.goto(url, wait_until="load", timeout=timeout_s * 1000)
        deadline = time.time() + timeout_s
        # usable: pins drawn and no long task for 500 ms
        while True:
            st = page.evaluate("""() => { const W = window.__forgeLoad, now = performance.now();
                const ends = W.longTasks.map(t => t[0] + t[1]);
                return { now, markersAt: W.markersAt, lastEnd: ends.length ? Math.max(...ends) : 0 }; }""")
            if st["markersAt"] is not None and st["now"] - max(st["lastEnd"], st["markersAt"]) >= 500:
                break
            if time.time() > deadline:
                raise TimeoutError("not usable in time")
            time.sleep(0.1)
        load = page.evaluate("""() => { const W = window.__forgeLoad;
            const nav = performance.getEntriesByType('navigation')[0] || {};
            const usable = W.longTasks.reduce((a, t) => Math.max(a, t[0] + t[1]), W.markersAt);
            const tbt = W.longTasks.filter(t => t[0] <= usable).reduce((a, t) => a + Math.max(0, t[1] - 50), 0);
            return { dom_content_loaded_ms: nav.domContentLoadedEventEnd, load_event_ms: nav.loadEventEnd,
                     pins_drawn_ms: W.markersAt, usable_ms: usable, blocking_ms: tbt,
                     transfer_bytes: nav.transferSize, long_tasks: W.longTasks.length,
                     heap_mb: performance.memory ? performance.memory.usedJSHeapSize / 1048576 : null,
                     marker_icons: document.querySelectorAll('.leaflet-marker-icon').length }; }""")
        res.update({k: (round(v, 1) if isinstance(v, float) else v) for k, v in load.items()})

        # panning: at the opening (country) view, then over a city
        res.update(_pan(page, "pan", timeout_s))
        moved = page.evaluate("""(c) => { const m = window.__forgeMap; if (!m) return false;
            m.setView([c[0], c[1]], c[2], { animate: false }); return true; }""", list(CITY_VIEW))
        if moved:
            _wait_quiet(page, timeout_s)
            res["city_marker_icons"] = page.evaluate("() => document.querySelectorAll('.leaflet-marker-icon').length")
            res.update(_pan(page, "city_pan", timeout_s))
            res.update(_idle(page, cdp, timeout_s))
    except Exception as e:  # noqa: BLE001
        res["failed"] = f"{e.__class__.__name__}: {str(e)[:200]}"
    res["page_errors"] = len(errors)
    if errors:
        res["first_page_error"] = errors[0][:200]
    ctx.close()
    return res


def _median_run(runs: list[dict]) -> dict:
    good = [r for r in runs if "failed" not in r]
    out = {"runs": len(runs), "failed_runs": len(runs) - len(good)}
    if not good:
        out["failed"] = runs[0].get("failed")
        return out
    for k, v in good[0].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals = [r[k] for r in good if isinstance(r.get(k), (int, float))]
            med = statistics.median(vals)
            out[k] = round(med, 3) if isinstance(med, float) else med
        elif k == "first_page_error":
            out[k] = v
    return out


def run(dist: Path = DIST, sizes=DEFAULT_SIZES, runs: int = 3, profiles=("computer", "phone"),
        include_today: bool = True, log=print) -> dict:
    from playwright.sync_api import sync_playwright

    dist = Path(dist)
    html = (dist / "index.html").read_text(encoding="utf-8")
    real_n = len(load_raw(html))
    tmp = Path(tempfile.mkdtemp(prefix="forge-loadtest-"))
    site = tmp / "site"
    shutil.copytree(dist, site)
    pages = []
    if include_today:
        pages.append(("today", real_n, "index.html", html))
    for n in sizes:
        page = synthetic_page(html, n)
        name = f"loadtest-{n}.html"
        (site / name).write_text(page, encoding="utf-8")
        pages.append((str(n), n, name, page))
    srv, base = serve(site)
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            version = browser.version
            # warm the script cache once, untimed
            measure_once(browser, base + "index.html", "computer")
            for label, n, name, page in pages:
                sizes_ = page_sizes(page)
                for prof in profiles:
                    rs = [measure_once(browser, base + name, prof) for _ in range(runs)]
                    row = {"pins": n, "label": label, "profile": prof, **sizes_, **_median_run(rs)}
                    results.append(row)
                    if log:
                        log(f"[load-test] {label:>6} pins {prof:<8} "
                            f"{row['html_gzip_bytes']/1024:,.0f} KB gz  usable {row.get('usable_ms')} ms  "
                            f"blocking {row.get('blocking_ms')} ms  pan p95 {row.get('pan_frame_p95_ms')}/{row.get('city_pan_frame_p95_ms')} ms  "
                            f"idle {row.get('idle_cpu_ms_per_s')} ms/s, {row.get('idle_running_pin_animations')} anims"
                            + (f"  FAILED {row.get('failed')}" if row.get("failed") else ""))
            browser.close()
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001
        commit = None
    return {
        "what": "FORGE Map load test: today's map page with made-up pins",
        "measured_at": datetime.now(SA_TZ).isoformat(timespec="minutes"),
        "commit": commit,
        "machine": {"python": platform.python_version(), "platform": platform.platform(),
                    "cpus": os.cpu_count(), "chromium": version},
        "method": {
            "real_pins_today": real_n, "runs_per_cell": runs, "value": "median of runs",
            "made_up_pins": "real pins cycled with new ids, positions moved up to 0.25 degrees",
            "scaled": ["var RAW marker list", "#gp-chart-data rows"],
            "unchanged": "everything else in index.html, as built",
            "tiles": "not fetched", "cdn_scripts": "fetched once, then served from memory",
            "profiles": {k: {"viewport": v["context"]["viewport"], "cpu_slowdown": v["cpu_slowdown"],
                             "network": v["network"]} for k, v in PROFILES.items() if k in profiles},
            "usable": "pins drawn and no long task for 500 ms; time from navigation start",
            "pan": "1.3 s drag across the map and back, then 1 s settle; once at the opening view, once over the Cape Flats at zoom 12",
            "idle": "3 s untouched over the Cape Flats at zoom 14: main-thread ms per second (CDP TaskDuration, slowed processor included), frame gaps, running pin animations",
        },
        "results": results,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FORGE Map load test with made-up pins.")
    ap.add_argument("--dist", type=Path, default=DIST)
    ap.add_argument("--sizes", default=",".join(str(s) for s in DEFAULT_SIZES))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--profiles", default="computer,phone")
    ap.add_argument("--out", type=Path, default=None, help="write the results as JSON here")
    args = ap.parse_args(argv)
    rep = run(args.dist, tuple(int(s) for s in args.sizes.split(",") if s), args.runs,
              tuple(args.profiles.split(",")))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
        print(f"[load-test] wrote {args.out}")
    return 1 if any(r.get("failed") for r in rep["results"]) else 0


if __name__ == "__main__":
    sys.exit(main())

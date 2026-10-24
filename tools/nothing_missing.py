"""Nothing-missing check (time-aware pins, Batch 1).

Every approved record in records.json must stay reachable through each way a
visitor finds a pin today:

  map        the pin is drawn: it is in the map page's markers with a usable
             position (map.html skips a marker whose lat or lng is null)
  link       a ?signal=<short id> link opens it: idxForSignal() takes the FIRST
             marker whose id or first 8 characters match, so a short id shared
             with another pin would open the wrong drawer
  search     search-index.json holds a signal document for it that points at
             index.html?signal=<short id> (base.html's site search)
  watchlist  the drawer star saves the full id and the Watchlist links back by
             pinShortId(full id) (watchlist.js), so the full id must pass
             watchlist.js's SIGNAL_ID_RE and come back to this pin
  area       Area check counts it: area-check.js gets every drawn marker, with a
             readable incident date (pinDays) that falls on the record's own
             incident day in South African time, and a usable uncertainty

This file reads a built dist/ only; it changes nothing. Later batches move pins
into month files, and this check (with the browser version in
tests/browser/nothing_missing_check.py) is what they must keep passing.

    python tools/nothing_missing.py            # check dist/
    python tools/nothing_missing.py --dist DIR # check another build
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

SA_TZ = timezone(timedelta(hours=2))          # South Africa: UTC+2, no daylight saving
ROUTES = ("map", "link", "search", "watchlist", "area")

# The same patterns the browser code uses.
LINK_PARAM_RE = re.compile(r"^[0-9a-f-]{8,36}$")            # map.html idxForSignal()
WATCHLIST_ID_RE = re.compile(r"^[0-9a-f-]{8,36}$", re.I)    # watchlist.js SIGNAL_ID_RE
RAW_RE = re.compile(r"var RAW = (\[.*?\]);\n", re.S)


def load_markers(html: str) -> list[dict]:
    """The map's markers, exactly as map.html embeds them (var RAW = [...])."""
    m = RAW_RE.search(html)
    if not m:
        raise ValueError("map page has no 'var RAW = [...]' marker list")
    return json.loads(m.group(1))


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _drawn(m: dict) -> bool:
    # map.html: `if (m.lat == null || m.lng == null) return;`
    return m.get("lat") is not None and m.get("lng") is not None


def parse_instant(s) -> datetime | None:
    """An ISO date-time as JavaScript's Date.parse reads the ones FORGE writes."""
    if not isinstance(s, str) or not s:
        return None
    t = s.strip().replace("Z", "+00:00")
    try:
        if len(t) == 10:
            # Date.parse('YYYY-MM-DD') is midnight UTC
            return datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def sa_day(d: datetime) -> str:
    return d.astimezone(SA_TZ).date().isoformat()


def record_incident_day(rec: dict) -> str | None:
    """The record's incident day in South African time, from records.json alone."""
    s = rec.get("incident_start")
    if not isinstance(s, str) or not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s                                     # a plain SA calendar day
    d = parse_instant(s)
    return sa_day(d) if d else None


def area_days(m: dict) -> dict | None:
    """area-check.js pinDays(): the SA days a marker covers, or None."""
    s = parse_instant(m.get("incident_at"))
    if not s:
        return None
    e = parse_instant(m.get("incident_end"))
    if not e or e < s:
        e = s
    end = max(s, e - timedelta(milliseconds=1))
    return {"start": sa_day(s), "end": sa_day(end)}


def load_dist(dist: Path = DIST) -> dict:
    dist = Path(dist)
    return {
        "records": json.loads((dist / "records.json").read_text(encoding="utf-8"))["records"],
        "markers": load_markers((dist / "index.html").read_text(encoding="utf-8")),
        "search": json.loads((dist / "search-index.json").read_text(encoding="utf-8")),
    }


def check(dist: Path = DIST, data: dict | None = None) -> dict:
    """Check every approved record against every route.

    Returns {"records": n, "routes": {route: reachable count}, "problems":
    [{"id", "short", "route", "why"}]}. No problems means nothing is missing.
    """
    data = data or load_dist(dist)
    records, markers, search = data["records"], data["markers"], data["search"]
    problems: list[dict] = []
    counts = {r: 0 for r in ROUTES}

    def bad(rec, route, why):
        problems.append({"id": rec.get("signal_id"), "short": str(rec.get("signal_id") or "")[:8],
                         "route": route, "why": why})

    def link_target(param: str) -> int | None:
        """map.html idxForSignal(): first drawn marker whose id or short id matches."""
        param = str(param or "").strip().lower()
        if not LINK_PARAM_RE.match(param):
            return None
        for i, m in enumerate(markers):
            mid = str(m.get("signal_id") or "").lower()
            if (mid == param or mid[:8] == param) and _drawn(m):
                return i
        return None

    by_id = {}
    for i, m in enumerate(markers):
        by_id.setdefault(str(m.get("signal_id") or "").lower(), i)
    search_by_signal: dict[str, list[dict]] = {}
    for doc in search:
        if doc.get("kind") == "signal" and doc.get("signal"):
            search_by_signal.setdefault(str(doc["signal"]).lower(), []).append(doc)

    seen = set()
    for rec in records:
        full = str(rec.get("signal_id") or "").lower()
        short = full[:8]
        if not full:
            bad(rec, "map", "record has no signal_id")
            continue
        if full in seen:
            bad(rec, "map", "record listed twice in records.json")
        seen.add(full)
        if rec.get("short_id") and str(rec["short_id"]).lower() != short:
            bad(rec, "link", f"records.json short_id {rec['short_id']!r} is not the first 8 of signal_id")

        # map
        idx = by_id.get(full)
        m = markers[idx] if idx is not None else None
        if m is None:
            bad(rec, "map", "not in the map page's markers")
        elif not _drawn(m):
            bad(rec, "map", "marker has no lat/lng, so it is never drawn")
        elif not (_finite(m["lat"]) and _finite(m["lng"])):
            bad(rec, "map", f"marker position is not a number: {m['lat']!r}, {m['lng']!r}")
        else:
            counts["map"] += 1
        drawn = m is not None and _drawn(m)

        # ?signal= link (short id, as Share, the ticker and search write it)
        target = link_target(short)
        if target is None:
            bad(rec, "link", f"?signal={short} opens nothing")
        elif target != idx:
            other = str(markers[target].get("signal_id"))[:36]
            bad(rec, "link", f"?signal={short} opens another pin ({other})")
        elif link_target(full) != idx:
            bad(rec, "link", "?signal=<full id> does not open it")
        else:
            counts["link"] += 1

        # search
        docs = search_by_signal.get(short, [])
        want_url = f"index.html?signal={short}"
        if not docs:
            bad(rec, "search", "no signal document in search-index.json")
        elif not any(d.get("url") == want_url for d in docs):
            bad(rec, "search", f"search result does not point at {want_url}")
        elif not any(str(d.get("title") or "").strip() for d in docs):
            bad(rec, "search", "search document has no title to match")
        elif len(docs) > 1:
            bad(rec, "search", "more than one search document claims this short id")
        else:
            counts["search"] += 1

        # Watchlist: saved under the full id, linked back by its short id
        if not WATCHLIST_ID_RE.match(full):
            bad(rec, "watchlist", "full id fails watchlist.js SIGNAL_ID_RE, so the star saves nothing")
        elif target is None or target != idx:
            bad(rec, "watchlist", "the saved link (?signal=short id) does not come back to this pin")
        else:
            counts["watchlist"] += 1

        # Area check
        if not drawn:
            bad(rec, "area", "not drawn, so Area check never sees it")
        else:
            unc = m.get("uncertainty_m") or 0
            days = area_days(m)
            want = record_incident_day(rec)
            if not _finite(unc) or unc < 0:
                bad(rec, "area", f"uncertainty_m is not a usable distance: {unc!r}")
            elif days is None:
                bad(rec, "area", "incident_at is unreadable, so date choices count it as 'date not known'")
            elif want is None:
                bad(rec, "area", "records.json incident_start is unreadable")
            elif not (days["start"] <= want <= days["end"]):
                bad(rec, "area", f"Area check dates it {days['start']}..{days['end']}, "
                                  f"the record says {want}")
            else:
                counts["area"] += 1

    return {"records": len(records), "routes": counts, "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dist", type=Path, default=DIST)
    args = ap.parse_args(argv)
    rep = check(args.dist)
    n = rep["records"]
    print(f"[nothing-missing] {n} approved records in {args.dist / 'records.json'}")
    for route in ROUTES:
        print(f"  {route:<10} {rep['routes'][route]} of {n} reachable")
    for p in rep["problems"]:
        print(f"  MISSING {p['short']} {p['route']}: {p['why']}")
    print("[nothing-missing] " + ("PASS" if not rep["problems"] else f"FAIL ({len(rep['problems'])} problems)"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

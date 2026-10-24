"""Nothing-missing check (time-aware pins, Batch 1).

Every approved record in records.json must stay reachable through each way a
visitor finds a pin today:

  map        the pin is drawn: it is in the map page's markers with a usable
             position (map.html skips a marker whose lat or lng is null).
             From Batch 3 the page carries only the map list, and opening a
             pin fetches pins/<short id>.json, so a build with a pins/
             folder also needs that file, readable and for this pin
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

From Batch 5 the page carries only the live months, and a pin from an older
month is reached through the shared loader (publisher/static/js/forge-pins.js):
archive/index.json's lookup names its month, and its month file holds it. For
such an archived pin:

  map        it is in its month file with a usable position and details file,
             and its month is not one the page should carry (a pin missing
             from a live month is still a gap). The browser keeps it off the
             live map by date and draws it while its drawer is open
  link       nothing on the page claims its short id, the index's lookup
             sends that short id to its month, and the month holds exactly
             one pin with it (ForgePins.findPin)
  watchlist  the saved ?signal= link comes back to it the same way
  area       a gap until Batch 6: Area check reads only the page's live list

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


PIN_DIR = "pins"   # tools/map_data.py PIN_DIR


def detail_status(dist: Path, markers: list[dict]) -> dict | None:
    """Batch 3 builds: {short id: None if pins/<short id>.json is readable and
    for this pin, else why not}. None for a build without a pins/ folder (from
    before Batch 3, when the page held every pin's details itself)."""
    folder = Path(dist) / PIN_DIR
    if not folder.is_dir():
        return None
    out: dict[str, str | None] = {}
    for m in markers:
        full = str(m.get("signal_id") or "")
        short = full[:8].lower()
        try:
            rec = json.loads((folder / f"{short}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            out[short] = f"no details file {PIN_DIR}/{short}.json, so its drawer cannot fill"
            continue
        except (json.JSONDecodeError, UnicodeDecodeError):
            out[short] = f"details file {PIN_DIR}/{short}.json is not readable JSON"
            continue
        if not isinstance(rec, dict) or str(rec.get("signal_id") or "") != full:
            out[short] = f"details file {PIN_DIR}/{short}.json is for another pin"
        else:
            out[short] = None
    return out


ARCHIVE_DIR = "archive"   # tools/map_archive.py


def load_archive(dist: Path) -> dict | None:
    """Batch 4+ builds: {"live_months": [...], "lookup": {short id: month},
    "months": {month: [pin entries]}}. None for a build without archive/."""
    index_path = Path(dist) / ARCHIVE_DIR / "index.json"
    if not index_path.is_file():
        return None
    index = json.loads(index_path.read_text(encoding="utf-8"))
    months: dict[str, list[dict]] = {}
    for row in index.get("months") or []:
        try:
            doc = json.loads((Path(dist) / row["file"]).read_text(encoding="utf-8"))
            months[row["month"]] = [f.get("properties") or {} for f in doc.get("features") or []]
        except (OSError, KeyError, ValueError):
            months[row.get("month", "?")] = []
    return {"live_months": list(index.get("live_months") or []),
            "lookup": {str(k).lower(): v for k, v in (index.get("pins") or {}).items()},
            "months": months}


def load_dist(dist: Path = DIST) -> dict:
    dist = Path(dist)
    markers = load_markers((dist / "index.html").read_text(encoding="utf-8"))
    archive = load_archive(dist)
    on_page = {str(m.get("signal_id") or "") for m in markers}
    archived = [e for entries in (archive or {}).get("months", {}).values() for e in entries
                if str(e.get("signal_id") or "") not in on_page]
    details = detail_status(dist, markers + archived)
    return {
        "records": json.loads((dist / "records.json").read_text(encoding="utf-8"))["records"],
        "markers": markers,
        "search": json.loads((dist / "search-index.json").read_text(encoding="utf-8")),
        "details": details,
        "archive": archive,
    }


def check(dist: Path = DIST, data: dict | None = None) -> dict:
    """Check every approved record against every route.

    Returns {"records": n, "routes": {route: reachable count}, "problems":
    [{"id", "short", "route", "why"}]}. No problems means nothing is missing.
    """
    data = data or load_dist(dist)
    records, markers, search = data["records"], data["markers"], data["search"]
    details = data.get("details")   # None: a build from before Batch 3
    archive = data.get("archive")   # None: a build from before Batch 4
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

    def archive_entry(full: str, short: str):
        """(month, entry or None, why) for a pin that is not on the page."""
        if archive is None:
            return None, None, "not in the map page's markers"
        month = archive["lookup"].get(short)
        if not month:
            return None, None, "not on the page's live list and not in the archive index's lookup"
        entries = archive["months"].get(month, [])
        hit = [e for e in entries if str(e.get("signal_id") or "").lower() == full]
        if not hit:
            return month, None, f"the archive index sends it to {month}, but that month file lacks it"
        if month in archive["live_months"]:
            return month, None, f"its month {month} should be on the page's live list, and it is not"
        return month, hit[0], ""

    def archive_link_ok(short: str, month: str) -> bool:
        """ForgePins.findPin(short id): the lookup's month holds exactly one pin with it."""
        if not LINK_PARAM_RE.match(short) or link_target(short) is not None:
            return False
        if archive["lookup"].get(short) != month:
            return False
        same = [e for e in archive["months"].get(month, [])
                if str(e.get("signal_id") or "").lower()[:8] == short]
        return len(same) == 1

    by_id = {}
    for i, m in enumerate(markers):
        by_id.setdefault(str(m.get("signal_id") or "").lower(), i)
    search_by_signal: dict[str, list[dict]] = {}
    for doc in search:
        if doc.get("kind") == "signal" and doc.get("signal"):
            search_by_signal.setdefault(str(doc["signal"]).lower(), []).append(doc)

    def check_archived(rec, full, short, month, e):
        """An older pin, reached through archive/index.json and its month file."""
        if not _drawn(e):
            bad(rec, "map", f"its {month} archive entry has no lat/lng, so it is never drawn")
        elif not (_finite(e["lat"]) and _finite(e["lng"])):
            bad(rec, "map", f"its {month} archive position is not a number")
        elif details is not None and details.get(short, "no details file checked") is not None:
            bad(rec, "map", details.get(short) or "no details file checked")
        else:
            counts["map"] += 1
        link_ok = archive_link_ok(short, month)
        if link_ok:
            counts["link"] += 1
        else:
            bad(rec, "link", f"?signal={short} does not reach it through the {month} archive")
        if not WATCHLIST_ID_RE.match(full):
            bad(rec, "watchlist", "full id fails watchlist.js SIGNAL_ID_RE, so the star saves nothing")
        elif not link_ok:
            bad(rec, "watchlist", "the saved link (?signal=short id) does not come back to this pin")
        else:
            counts["watchlist"] += 1
        docs = search_by_signal.get(short, [])
        if docs and any(d.get("url") == f"index.html?signal={short}" for d in docs) \
                and any(str(d.get("title") or "").strip() for d in docs) and len(docs) == 1:
            counts["search"] += 1
        else:
            bad(rec, "search", "no single titled search document pointing at its ?signal= link")
        bad(rec, "area", f"archived in {month}: Area check reads only the page's live list "
                         "until Batch 6 loads month files")

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
            month, entry, why = archive_entry(full, short)
            if entry is not None:
                check_archived(rec, full, short, month, entry)
                continue
            bad(rec, "map", why)
        elif not _drawn(m):
            bad(rec, "map", "marker has no lat/lng, so it is never drawn")
        elif not (_finite(m["lat"]) and _finite(m["lng"])):
            bad(rec, "map", f"marker position is not a number: {m['lat']!r}, {m['lng']!r}")
        elif details is not None and details.get(short, "no details file checked") is not None:
            bad(rec, "map", details.get(short) or "no details file checked")
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

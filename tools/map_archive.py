"""Month archive files and the archive index (time-aware pins, Batch 4).

Every pin gets a permanent home in one calendar month: the month of its
incident_start, read in South African time (UTC+2, no daylight saving). A
date-only incident_start ("2026-10-31") is that local day. A date-time is
converted to UTC+2 first, so 2026-10-31T23:30:00Z is 1 November.

The build writes, under dist/archive/:

- one GeoJSON file per month that holds pins, named with a hash of its
  content (2026-10.<10 hex>.json) so it can be cached for a year: any change
  to the month (a correction, a retraction, a new pin, a new coverage note)
  gives it a new name, and a month that did not change keeps its name. Each
  feature's properties are the pin's map-list entry (tools/map_data.py
  MAP_FIELDS), the same shape as the page's `var RAW` list;
- index.json, not hashed: the months (count, file, closed or not, first and
  last incident day, coverage note) and a short lookup from pin id (first 8
  characters, as in ?signal= links) to month. It carries no titles: site
  search (search-index.json) already lists every pin and opens archived
  ones, and the browser fetches this whole file before it opens any
  archived pin, so a title list here only made that wait longer (dropped
  in Batch 6).

The live set the page carries (`var RAW`) is this month's and last month's
pins, by the build day in South African time (and the month before as well
on the first days of March, so the 30-day window always fits). The exact 30-day cut is made
in the browser from today's date (Batch 5), so a pin still leaves the live
map on time when nothing is published. Month files never decide what the
live map shows; they are where a pin is always found.

Nothing reads these files in the browser yet: Batch 5's shared loader does.
`problems(dist)` is the after-build check that the live list and the month
files together hold every map pin exactly once in its own month.

    python tools/map_archive.py            # check dist/
    python tools/map_archive.py --dist DIR # check another build
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import map_data  # noqa: E402

SAST = timezone(timedelta(hours=2))
ARCHIVE_DIR = "archive"
INDEX_NAME = "index.json"
SCHEMA = "forge-map-archive/1"
WINDOW_DAYS = 30
HASH_LEN = 10
MONTH_FILE_RE = re.compile(r"^(\d{4}-\d{2})\.([0-9a-f]{%d})\.json$" % HASH_LEN)
COVERAGE_FILE = ROOT / "docs" / "signals" / "month-coverage.json"

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")

# Used for a month with no note of its own in docs/signals/month-coverage.json.
DEFAULT_COVERAGE_NOTE = (
    "Hand-checked incidents approved from this month's collection rounds. "
    "An empty day means it was not searched or nothing was approved, not that "
    "nothing happened. This is not a crime rate."
)


class ArchiveError(ValueError):
    """A pin can't be given a month (no readable incident date)."""


# ── Dates ───────────────────────────────────────────────────────────────────────

def local_day(value) -> date | None:
    """The South African calendar day of an ISO date or date-time, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SAST)          # FORGE writes local times with +02:00
    return dt.astimezone(SAST).date()


def incident_day(entry: dict) -> date | None:
    """The day a pin's age counts from: incident_start (the settled age clock).
    A machine-collected pin with no incident_start falls back to incident_at,
    then published_at, so it still gets a month. Hand-checked pins always
    have incident_start (tools/checked_signals.py requires it)."""
    for key in ("incident_start", "incident_at", "published_at"):
        d = local_day(entry.get(key))
        if d is not None:
            return d
    return None


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_of(entry: dict) -> str:
    d = incident_day(entry)
    if d is None:
        raise ArchiveError(f"pin {map_data.short_id(entry.get('signal_id'))} "
                           f"({entry.get('title')!r}) has no readable incident date")
    return month_key(d)


def month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{MONTH_NAMES[int(m) - 1]} {y}"


def previous_month(key: str) -> str:
    y, m = (int(x) for x in key.split("-"))
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def build_today() -> date:
    """Today in South Africa. Tests replace this to build as if on another day."""
    return datetime.now(SAST).date()


def live_months(today: date) -> tuple[str, ...]:
    """The months the page's live list carries: last month and this month,
    plus any earlier month the window still reaches. The browser's 30-day
    cut (Batch 5) must always find its pins in the live list, so the list
    reaches back WINDOW_DAYS days whole (today minus 30, a day of slack
    whichever way Batch 5 counts the edge). That needs a third month only
    on the first days of March, after a short February."""
    months = {month_key(today), previous_month(month_key(today))}
    for back in range(WINDOW_DAYS + 1):
        months.add(month_key(today - timedelta(days=back)))
    return tuple(sorted(months))


# ── Coverage notes ─────────────────────────────────────────────────────────────

def load_coverage(path: pathlib.Path | None = None) -> dict[str, str]:
    """month -> written coverage note, from docs/signals/month-coverage.json."""
    p = pathlib.Path(path or COVERAGE_FILE)
    try:
        months = json.loads(p.read_text(encoding="utf-8")).get("months") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for k, v in months.items():
        note = v.get("note") if isinstance(v, dict) else v
        if re.fullmatch(r"\d{4}-\d{2}", str(k)) and isinstance(note, str) and note.strip():
            out[str(k)] = note.strip()
    return out


# ── Building ───────────────────────────────────────────────────────────────────

def _feature(entry: dict) -> dict:
    return {
        "type": "Feature",
        "id": entry.get("signal_id"),
        "geometry": {"type": "Point", "coordinates": [entry.get("lng"), entry.get("lat")]},
        "properties": entry,
    }


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def content_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:HASH_LEN]


def _incident_sort(entry: dict) -> tuple:
    return (str(entry.get("incident_start") or ""), str(entry.get("signal_id") or ""))


def split(markers: list[dict], today: date) -> tuple[list[dict], dict[str, list[dict]]]:
    """(live markers in their given order, month -> map entries). Every
    marker lands in exactly one month; the live list is the markers whose
    month is last month or this month."""
    keep = set(live_months(today))
    months: dict[str, list[dict]] = {}
    live = []
    for m in markers:
        key = month_of(m)
        months.setdefault(key, []).append(map_data.map_entry(m))
        if key in keep:
            live.append(m)
    for key in months:
        months[key].sort(key=_incident_sort)
    return live, months


def month_doc(key: str, entries: list[dict], note: str, note_source: str) -> dict:
    return {
        "type": "FeatureCollection",
        "schema": SCHEMA,
        "month": key,
        "label": month_label(key),
        "timezone": "South African time (UTC+2)",
        "count": len(entries),
        "coverage_note": note,
        "coverage_note_source": note_source,
        "features": [_feature(e) for e in entries],
    }


def write_archive(dist: pathlib.Path, markers: list[dict], today: date | None = None,
                  coverage: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    """Write dist/archive/ afresh (old hashed files are cleared, so a month
    that changed leaves no stale copy). Returns (live markers, index)."""
    today = today or build_today()
    coverage = load_coverage() if coverage is None else coverage
    live, months = split(markers, today)
    out = pathlib.Path(dist) / ARCHIVE_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    this_month = month_key(today)
    rows, lookup = [], {}
    for key in sorted(months):
        entries = months[key]
        note = coverage.get(key)
        doc = month_doc(key, entries, note or DEFAULT_COVERAGE_NOTE, "written" if note else "default")
        body = _dump(doc)
        name = f"{key}.{content_hash(body)}.json"
        (out / name).write_bytes(body)
        days = [incident_day(e) for e in entries]
        rows.append({
            "month": key,
            "label": month_label(key),
            "count": len(entries),
            "file": f"{ARCHIVE_DIR}/{name}",
            "closed": key < this_month,
            "first_incident": min(days).isoformat(),
            "last_incident": max(days).isoformat(),
            "coverage_note": doc["coverage_note"],
            "coverage_note_source": doc["coverage_note_source"],
        })
        for e in entries:
            lookup[map_data.short_id(e["signal_id"])] = key
    index = {
        "schema": SCHEMA,
        "built_for": today.isoformat(),
        "timezone": "South African time (UTC+2)",
        "window_days": WINDOW_DAYS,
        "live_months": list(live_months(today)),
        "count": sum(r["count"] for r in rows),
        "months": rows,
        "pins": dict(sorted(lookup.items())),
    }
    (out / INDEX_NAME).write_bytes(_dump(index))
    return live, index


# ── Reading ────────────────────────────────────────────────────────────────────

def read_index(dist: pathlib.Path) -> dict:
    return json.loads((pathlib.Path(dist) / ARCHIVE_DIR / INDEX_NAME).read_text(encoding="utf-8"))


def read_month(dist: pathlib.Path, row: dict) -> dict:
    return json.loads((pathlib.Path(dist) / row["file"]).read_text(encoding="utf-8"))


def month_entries(dist: pathlib.Path) -> dict[str, list[dict]]:
    """month -> its pins' map entries, as the month files hold them."""
    idx = read_index(dist)
    return {r["month"]: [f["properties"] for f in read_month(dist, r)["features"]]
            for r in idx["months"]}


def all_entries(dist: pathlib.Path) -> list[dict]:
    """Every pin's map entry: the live list plus every month, once each."""
    seen, out = set(), []
    live = map_data.read_map_list(pathlib.Path(dist) / "index.html")
    months = month_entries(dist) if (pathlib.Path(dist) / ARCHIVE_DIR).is_dir() else {}
    for e in live + [e for k in sorted(months) for e in months[k]]:
        sid = e.get("signal_id")
        if sid not in seen:
            seen.add(sid)
            out.append(e)
    return out


# ── After-build check ──────────────────────────────────────────────────────────

def problems(dist: pathlib.Path, expected_ids: set[str] | None = None) -> list[str]:
    """The live list and the month files agree: every map pin is in exactly
    one month file, its own month; each file's name carries its hash; counts
    and lookup match, and the index carries no search list (Batch 6); the live list is exactly the pins of the
    index's live months; no leftover files. With expected_ids (the active
    checked pins), the months must hold exactly that set."""
    dist = pathlib.Path(dist)
    folder = dist / ARCHIVE_DIR
    if not folder.is_dir():
        return [f"no {ARCHIVE_DIR}/ folder next to index.html"]
    try:
        idx = read_index(dist)
    except FileNotFoundError:
        return [f"no {ARCHIVE_DIR}/{INDEX_NAME}"]
    except (ValueError, json.JSONDecodeError) as exc:
        return [f"{ARCHIVE_DIR}/{INDEX_NAME} is not readable ({exc})"]
    try:
        live = map_data.read_map_list(dist / "index.html")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"map list is not readable ({exc})"]
    out: list[str] = []
    if idx.get("schema") != SCHEMA:
        out.append(f"archive index schema is {idx.get('schema')!r}, not {SCHEMA}")
    home: dict[str, str] = {}          # full id -> month file it is in
    by_id: dict[str, dict] = {}
    listed = set()
    for row in idx.get("months", []):
        key, rel = row.get("month"), str(row.get("file") or "")
        name = rel.split("/")[-1]
        fm = MONTH_FILE_RE.match(name)
        if not rel.startswith(ARCHIVE_DIR + "/") or not fm or fm.group(1) != key:
            out.append(f"month {key}: file name {rel!r} is not {ARCHIVE_DIR}/{key}.<hash>.json")
            continue
        listed.add(name)
        try:
            body = (folder / name).read_bytes()
            doc = json.loads(body)
        except FileNotFoundError:
            out.append(f"month {key}: {rel} is missing")
            continue
        except (ValueError, json.JSONDecodeError) as exc:
            out.append(f"month {key}: {rel} is not readable ({exc})")
            continue
        if content_hash(body) != fm.group(2):
            out.append(f"month {key}: {rel} does not match the hash in its name")
        if doc.get("type") != "FeatureCollection" or doc.get("month") != key:
            out.append(f"month {key}: {rel} is not that month's FeatureCollection")
        feats = doc.get("features") or []
        if not (row.get("count") == doc.get("count") == len(feats)):
            out.append(f"month {key}: counts disagree (index {row.get('count')}, "
                       f"file {doc.get('count')}, features {len(feats)})")
        if not feats:
            out.append(f"month {key}: listed with no pins")
        if row.get("coverage_note") != doc.get("coverage_note") or not doc.get("coverage_note"):
            out.append(f"month {key}: coverage note missing or differs between index and file")
        for f in feats:
            p = f.get("properties") or {}
            sid = str(p.get("signal_id") or "")
            short = map_data.short_id(sid)
            if sid in home:
                out.append(f"{short} is in two months: {home[sid]} and {key}"
                           if home[sid] != key else f"{short} is listed twice in {key}")
                continue
            home[sid] = key
            by_id[sid] = p
            try:
                own = month_of(p)
            except ArchiveError as exc:
                out.append(str(exc))
                continue
            if own != key:
                out.append(f"{short} is filed in {key} but its incident month is {own}")
            coords = (f.get("geometry") or {}).get("coordinates")
            if coords != [p.get("lng"), p.get("lat")]:
                out.append(f"{short}: position in {key} differs from its properties")
            if idx.get("pins", {}).get(short) != key:
                out.append(f"{short}: archive lookup says {idx.get('pins', {}).get(short)!r}, file says {key}")
    if len(idx.get("pins", {})) != len(home):
        out.append(f"archive lookup lists {len(idx.get('pins', {}))} pins, the month files hold {len(home)}")
    if idx.get("count") != len(home):
        out.append(f"archive index count {idx.get('count')} but the month files hold {len(home)}")
    if "search" in idx:
        out.append("archive index carries a search list; site search covers every pin, so the index must not")
    keep = set(idx.get("live_months") or [])
    live_ids = set()
    for e in live:
        sid = str(e.get("signal_id") or "")
        short = map_data.short_id(sid)
        if sid in live_ids:
            out.append(f"{short} is in the live list twice")
        live_ids.add(sid)
        if sid not in home:
            out.append(f"{short} is on the live map but in no month file")
        elif home[sid] not in keep:
            out.append(f"{short} is on the live list but its month {home[sid]} is not live")
        elif map_data.map_entry(e) != map_data.map_entry(by_id[sid]):
            out.append(f"{short}: live list and month file disagree")
    for sid, key in home.items():
        if key in keep and sid not in live_ids:
            out.append(f"{map_data.short_id(sid)} ({key}) belongs on the live list but is missing")
    if expected_ids is not None:
        for sid in sorted(set(expected_ids) - set(home)):
            out.append(f"not in any month file: {map_data.short_id(sid)}")
        for sid in sorted(set(home) - set(expected_ids)):
            out.append(f"in a month file but not an active pin: {map_data.short_id(sid)}")
    for p in sorted(folder.iterdir()):
        if p.name != INDEX_NAME and p.name not in listed:
            out.append(f"leftover file in {ARCHIVE_DIR}/: {p.name}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    args = ap.parse_args(argv)
    probs = problems(pathlib.Path(args.dist))
    if probs:
        for p in probs:
            print("PROBLEM:", p)
        return 1
    idx = read_index(pathlib.Path(args.dist))
    for r in idx["months"]:
        print(f"{r['month']}: {r['count']} pins -> {r['file']}")
    print(f"OK: {idx['count']} pins in {len(idx['months'])} month file(s); live months {idx['live_months']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Checked signals: the curated layer of hand-checked pins (Sprint 1, 18 Oct 2026).

The JSON files in docs/signals are the source of truth for every hand-checked
pin on the map. database.db is not in git and Vercel serves the committed
dist/, so a rebuild from a database that is missing a batch would silently
drop pins. This tool is the one way those pins get into a database, and the
check that publish.py runs before and after every build.

It is deliberately NOT an FMS module (forge_modules/) or a collector
(forage/collectors/): both feed the automatic engine, which these pins must
skip. Imported pins are written already processed (status 'reviewed',
processed_at set, no gravity score) with source_type 'checked', so ingest
never re-scores them.

Commands (run from the repo root):
    python tools/checked_signals.py import            # dry run: shows what would change
    python tools/checked_signals.py import --write    # apply every batch in index order
    python tools/checked_signals.py verify            # the rebuild guard on its own
    python tools/checked_signals.py stamp             # refresh fingerprints after a hand edit
    python tools/checked_signals.py stamp --check     # fail if any fingerprint is stale

All commands take --db PATH (default: the repo's database.db, the same file
publish.py reads) and --index PATH (default docs/signals/index.json).

File format (version 2), one file per batch, listed in load order in the index:
    index.json  {"schema": "forge.checked_signals.index", "version": 1,
                 "batches": [{"file": "...json", "province": "Western Cape",
                              "signals": 5}, ...]}
    batch file  {"schema": "forge.checked_signals.batch", "version": 2,
                 "province": "Western Cape", "batch": "...", "rules": "...",
                 "retractions": [{"signal_id": "...", "reason": "...",
                                  "retracted_at": "2026-10-20T08:00:00Z"}],
                 "signals": [{signal_id, external_id, source, title, content,
                              lat, lng, stream, timestamp, published_at,
                              metadata: {..., checked: {...}},
                              fingerprint: "sha256:..."}]}
    metadata.checked carries, beside the free-text "incident" and "reported":
        incident_start      ISO 8601: "2026-10-16T06:00+02:00" or "2026-10-14"
        incident_precision  time | approx_time | part_of_day | day
        incident_part       morning | afternoon | evening | night (part_of_day only)
        incident_event      occurred | found (a body found, time of death unknown)
        reported_at         ISO 8601 date of the first report

Retracting a pin: add it to its batch's "retractions" with a reason. The
import withdraws it (published_at cleared, status 'dismissed' because the
schema's CHECK allows no 'retracted' value) and keeps the reason in
metadata_json["retracted"]. The pin's record stays in the file as history.

Link records (Sprint 3): metadata.checked.links is an optional list of the
pins this one is related to. Nothing links pins except these records, and
only an approved incident link draws a line on the map:
    {"to": "<signal_id of another checked pin>",
     "type": "incident" | "shared_source" | "similar_pattern",
     "label": "Same shooting"               short text for the line or drawer,
     "basis": {"quote": "...", "url": "https://..."},
     "approved_by": "mk", "approved_at": "2026-10-19"}
    incident         the sources say it is the same or a connected incident.
                     Needs both a quote and a link. Drawn as one labelled line.
    shared_source    same police statement, spokesperson, reporter or
                     community source: a paper trail, not a crime link.
    similar_pattern  a similarity a person noticed (method, setting).
    The last two never draw a line; they show as labelled text in the drawer.
A link is recorded once, on either pin; both drawers show it. Adding or
changing one is a hand edit, so restamp the fingerprint afterwards.

Machine traces: the engines skip checked pins (Sprint 3), and the import also
clears what an engine could leave outside the fingerprint (cluster_id,
confidence_score, relevance_score and signal_entities name rows). verify
fails if any of them is back.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.db import paths as _paths  # noqa: E402

# FORGE_DB points every tool at another database (an isolated test copy).
# Path rules live in core.db.paths. Under FORGE_ROLE=staging a missing or refused
# FORGE_DB leaves DB_PATH as None; file checks (stamp, load_index) still work and
# anything that needs the database refuses with the reason.
try:
    DB_PATH: pathlib.Path | None = _paths.resolve_db_path(ROOT / "database.db")
    _DB_PATH_ERROR: str | None = None
except _paths.DBPathError as _exc:
    DB_PATH = None
    _DB_PATH_ERROR = str(_exc)
INDEX_PATH = ROOT / "docs" / "signals" / "index.json"

INDEX_SCHEMA = "forge.checked_signals.index"
BATCH_SCHEMA = "forge.checked_signals.batch"
INDEX_VERSIONS = {1}
BATCH_VERSIONS = {2}

SOURCE_TYPE = "checked"
RETRACTED_STATUS = "dismissed"   # signals.status CHECK has no 'retracted'

FIELDS = ("signal_id", "external_id", "source", "title", "content", "lat", "lng",
          "stream", "timestamp", "published_at", "metadata")
STREAMS = {"GLOBAL", "CRIME_INTEL", "INFRASTRUCTURE", "PRIORITY"}
PRECISIONS = {"time", "approx_time", "part_of_day", "day"}
PARTS_OF_DAY = {"morning", "afternoon", "evening", "night"}
EVENTS = {"occurred", "found"}
LINK_TYPES = {"incident", "shared_source", "similar_pattern"}
LINE_LINK_TYPES = {"incident"}          # the only kind that draws a map line
LINK_LABEL_MAX = 80
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Generous bounding boxes, a sanity check against swapped or mistyped
# coordinates, not a border test. (forge_modules/geo_enrichment's boxes are too
# tight here: they cut off Cape Agulhas, Knysna and Plettenberg Bay.)
PROVINCE_BOUNDS = {
    "Western Cape": {"lat": (-34.90, -30.40), "lng": (17.70, 24.30)},
    "North West": {"lat": (-28.10, -24.50), "lng": (22.50, 28.40)},
    "Eastern Cape": {"lat": (-34.30, -30.00), "lng": (22.50, 30.30)},
    "Limpopo": {"lat": (-25.50, -22.10), "lng": (26.30, 31.95)},
    "Mpumalanga": {"lat": (-27.85, -24.40), "lng": (28.15, 32.10)},
}

# The Sprint 5 pattern test (private/sprint5/predictions.md) is about Western
# Cape killings only. Pins from any other province are published on the map but
# never feed its predictions, scoring, thresholds or the arrest-gap comparison.
PATTERN_TEST_PROVINCE = "Western Cape"


class CheckedSignalError(ValueError):
    """A checked-signal file or record that must not be imported."""


# ── Parsing and fingerprints ──────────────────────────────────────────────────

def _parse_iso(value, what: str, sid: str) -> datetime | date:
    """Read an ISO 8601 date or date-time; raise on anything else."""
    if not isinstance(value, str) or not value.strip():
        raise CheckedSignalError(f"{sid}: {what} is missing or not text")
    v = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise CheckedSignalError(f"{sid}: {what} {v!r} is not a real date") from None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise CheckedSignalError(f"{sid}: {what} {v!r} is not an ISO 8601 date") from None


def _day(d: datetime | date) -> date:
    return d.date() if isinstance(d, datetime) else d


# Sprint 4: one machine-readable incident time for everything that sorts or
# matches by time. The signal's own `timestamp` is the report time.
_SAST = timezone(timedelta(hours=2))
# Local-time span (start hour, hours long) of each part of the day.
_PART_SPANS = {"morning": (5, 7), "afternoon": (12, 5), "evening": (17, 4), "night": (20, 9)}


def incident_window(chk: dict) -> tuple[datetime, datetime] | None:
    """Earliest and latest UTC moment the incident can have started.

    time / approx_time: the stated moment (approx: +/- 1 hour).
    part_of_day: that part of the stated local day.  day: the whole local day.
    None when the record has no readable incident_start."""
    if not isinstance(chk, dict):
        return None
    try:
        start = _parse_iso(chk.get("incident_start"), "incident_start", "?")
    except CheckedSignalError:
        return None
    prec = chk.get("incident_precision") or "day"
    if isinstance(start, datetime):
        if start.tzinfo is None:
            start = start.replace(tzinfo=_SAST)
        pad = timedelta(hours=1) if prec == "approx_time" else timedelta(0)
        return (start - pad).astimezone(timezone.utc), (start + pad).astimezone(timezone.utc)
    day0 = datetime(start.year, start.month, start.day, tzinfo=_SAST)
    if prec == "part_of_day" and chk.get("incident_part") in _PART_SPANS:
        h, n = _PART_SPANS[chk["incident_part"]]
        a = day0 + timedelta(hours=h)
        return a.astimezone(timezone.utc), (a + timedelta(hours=n)).astimezone(timezone.utc)
    return day0.astimezone(timezone.utc), (day0 + timedelta(days=1)).astimezone(timezone.utc)


def incident_sort_key(chk: dict) -> str:
    """UTC ISO string of the incident's earliest moment, '' when unknown.
    Sorts October in incident order, whatever the report or publish time."""
    w = incident_window(chk)
    return w[0].strftime("%Y-%m-%dT%H:%M:%SZ") if w else ""


def canonical(record: dict) -> str:
    """Stable JSON for a pin, without its fingerprint."""
    body = {k: record[k] for k in FIELDS if k in record}
    body["lat"] = float(body["lat"])
    body["lng"] = float(body["lng"])
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(record: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


# ── Validation ────────────────────────────────────────────────────────────────

def validate_signal(s: dict, province: str | None = None, require_v2: bool = True) -> None:
    """Raise CheckedSignalError unless the pin is safe to import."""
    if not isinstance(s, dict):
        raise CheckedSignalError("a signal record is not an object")
    sid = str(s.get("signal_id") or "?")
    missing = [k for k in FIELDS if s.get(k) in (None, "")]
    if missing:
        raise CheckedSignalError(f"{sid}: missing {missing}")
    meta = s["metadata"]
    if not isinstance(meta, dict) or not isinstance(meta.get("checked"), dict):
        raise CheckedSignalError(f"{sid}: metadata.checked is required")
    if s["stream"] not in STREAMS:
        raise CheckedSignalError(f"{sid}: unknown stream {s['stream']!r}")
    try:
        lat, lng = float(s["lat"]), float(s["lng"])
    except (TypeError, ValueError):
        raise CheckedSignalError(f"{sid}: lat/lng are not numbers") from None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise CheckedSignalError(f"{sid}: lat/lng out of range")
    if province is not None:
        box = PROVINCE_BOUNDS.get(province)
        if box is None:
            raise CheckedSignalError(f"{sid}: no bounds known for province {province!r}")
        if not (box["lat"][0] <= lat <= box["lat"][1] and box["lng"][0] <= lng <= box["lng"][1]):
            raise CheckedSignalError(f"{sid}: {lat}, {lng} is outside {province}")
    chk = meta["checked"]
    srcs = [x for x in (chk.get("sources") or [])
            if isinstance(x, dict) and str(x.get("url") or "").lower().startswith(("https://", "http://"))]
    if not srcs:
        raise CheckedSignalError(f"{sid}: no source with a web link")
    for k in ("timestamp", "published_at"):
        _parse_iso(s[k], k, sid)
    if not require_v2:
        return
    extra = set(s) - set(FIELDS) - {"fingerprint"}
    if extra:
        raise CheckedSignalError(f"{sid}: unknown fields {sorted(extra)}")
    for k in ("incident", "reported", "checked_at"):
        if not chk.get(k):
            raise CheckedSignalError(f"{sid}: checked.{k} is missing")
    _parse_iso(chk["checked_at"], "checked.checked_at", sid)
    start = _parse_iso(chk.get("incident_start"), "checked.incident_start", sid)
    prec = chk.get("incident_precision")
    if prec not in PRECISIONS:
        raise CheckedSignalError(f"{sid}: checked.incident_precision must be one of {sorted(PRECISIONS)}")
    has_time = isinstance(start, datetime)
    if prec in ("time", "approx_time") and not (has_time and start.tzinfo):
        raise CheckedSignalError(f"{sid}: precision {prec} needs a date-time with a UTC offset")
    if prec in ("part_of_day", "day") and has_time:
        raise CheckedSignalError(f"{sid}: precision {prec} takes a date only")
    if prec == "part_of_day" and chk.get("incident_part") not in PARTS_OF_DAY:
        raise CheckedSignalError(f"{sid}: part_of_day needs checked.incident_part in {sorted(PARTS_OF_DAY)}")
    if chk.get("incident_event") not in EVENTS:
        raise CheckedSignalError(f"{sid}: checked.incident_event must be one of {sorted(EVENTS)}")
    reported = _parse_iso(chk.get("reported_at"), "checked.reported_at", sid)
    if _day(reported) < _day(start):
        raise CheckedSignalError(f"{sid}: reported_at is before incident_start")
    fp = s.get("fingerprint")
    if not fp:
        raise CheckedSignalError(f"{sid}: fingerprint is missing (run: checked_signals.py stamp)")
    if fp != fingerprint(s):
        raise CheckedSignalError(
            f"{sid}: fingerprint does not match the record. If the edit is intended, "
            f"run: python tools/checked_signals.py stamp")


@dataclass
class Batch:
    path: pathlib.Path
    province: str
    signals: list[dict]
    retractions: list[dict] = field(default_factory=list)


@dataclass
class CheckedSet:
    batches: list[Batch]

    @property
    def signals(self) -> list[dict]:
        return [s for b in self.batches for s in b.signals]

    @property
    def retracted(self) -> dict[str, dict]:
        return {r["signal_id"]: r for b in self.batches for r in b.retractions}

    @property
    def active(self) -> list[dict]:
        gone = self.retracted
        return [s for s in self.signals if s["signal_id"] not in gone]


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CheckedSignalError(f"{path}: file not found") from None
    except json.JSONDecodeError as exc:
        raise CheckedSignalError(f"{path}: not valid JSON ({exc})") from None


def load_batch_file(path: pathlib.Path, province: str | None = None) -> Batch:
    data = _read_json(path)
    if not isinstance(data, dict) or data.get("schema") != BATCH_SCHEMA:
        raise CheckedSignalError(f"{path.name}: not a checked-signal batch (schema {BATCH_SCHEMA})")
    if data.get("version") not in BATCH_VERSIONS:
        raise CheckedSignalError(f"{path.name}: unsupported version {data.get('version')!r}")
    prov = data.get("province")
    if province is not None and prov != province:
        raise CheckedSignalError(f"{path.name}: province {prov!r} does not match the index ({province!r})")
    signals = data.get("signals")
    if not isinstance(signals, list) or not signals:
        raise CheckedSignalError(f"{path.name}: no signals")
    for s in signals:
        validate_signal(s, prov)
    ids = {s["signal_id"] for s in signals}
    retractions = data.get("retractions")
    if not isinstance(retractions, list):
        raise CheckedSignalError(f"{path.name}: retractions must be a list (use [] for none)")
    for r in retractions:
        if not isinstance(r, dict) or not r.get("signal_id") or not str(r.get("reason") or "").strip():
            raise CheckedSignalError(f"{path.name}: each retraction needs a signal_id and a reason")
        if r["signal_id"] not in ids:
            raise CheckedSignalError(f"{path.name}: retraction for {r['signal_id']} which is not in this batch")
        _parse_iso(r.get("retracted_at"), "retracted_at", r["signal_id"])
    return Batch(path=path, province=prov, signals=signals, retractions=retractions)


def load_index(index_path: pathlib.Path = INDEX_PATH) -> CheckedSet:
    """Load and validate every batch the index lists, in load order."""
    data = _read_json(index_path)
    if not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA:
        raise CheckedSignalError(f"{index_path.name}: not a checked-signal index (schema {INDEX_SCHEMA})")
    if data.get("version") not in INDEX_VERSIONS:
        raise CheckedSignalError(f"{index_path.name}: unsupported version {data.get('version')!r}")
    entries = data.get("batches")
    if not isinstance(entries, list) or not entries:
        raise CheckedSignalError(f"{index_path.name}: lists no batches")
    batches, seen_files = [], set()
    for e in entries:
        name = e.get("file") if isinstance(e, dict) else None
        if not name or "/" in name or "\\" in name:
            raise CheckedSignalError(f"{index_path.name}: bad batch entry {e!r}")
        if name in seen_files:
            raise CheckedSignalError(f"{index_path.name}: {name} is listed twice")
        seen_files.add(name)
        b = load_batch_file(index_path.parent / name, e.get("province"))
        if e.get("signals") != len(b.signals):
            raise CheckedSignalError(
                f"{name}: index says {e.get('signals')} signals, the file has {len(b.signals)}")
        batches.append(b)
    cs = CheckedSet(batches)
    _reject_duplicates(cs.signals)
    validate_links(cs)
    return cs


def provinces(cs: "CheckedSet") -> dict[str, str]:
    """signal_id -> the province its batch file declares."""
    return {s["signal_id"]: b.province for b in cs.batches for s in b.signals}


def pattern_test_pins(cs: "CheckedSet") -> list[dict]:
    """The active pins the Western Cape pattern test may use. Everything the
    Sprint 5 runs count must come from here, never from cs.active."""
    prov = provinces(cs)
    return [s for s in cs.active if prov.get(s["signal_id"]) == PATTERN_TEST_PROVINCE]


def _is_url(v) -> bool:
    return str(v or "").strip().lower().startswith(("https://", "http://"))


def validate_links(cs: CheckedSet) -> None:
    """Every link record must point at another checked pin, say what it is,
    rest on a quote or link, and name who approved it."""
    known = {s["signal_id"] for s in cs.signals}
    pairs: dict[tuple, str] = {}
    for s in cs.signals:
        sid = s["signal_id"]
        links = s["metadata"]["checked"].get("links")
        if links is None:
            continue
        if not isinstance(links, list):
            raise CheckedSignalError(f"{sid}: checked.links must be a list")
        for i, ln in enumerate(links):
            where = f"{sid}: checked.links[{i}]"
            if not isinstance(ln, dict):
                raise CheckedSignalError(f"{where} is not an object")
            extra = set(ln) - {"to", "type", "label", "basis", "approved_by", "approved_at", "note"}
            if extra:
                raise CheckedSignalError(f"{where}: unknown fields {sorted(extra)}")
            to = str(ln.get("to") or "").strip().lower()
            if not _UUID_RE.match(to):
                raise CheckedSignalError(f"{where}: 'to' must be the full signal_id of a checked pin")
            if to == sid:
                raise CheckedSignalError(f"{where}: a pin cannot link to itself")
            if to not in known:
                raise CheckedSignalError(f"{where}: {to} is not a checked pin in any batch")
            kind = ln.get("type")
            if kind not in LINK_TYPES:
                raise CheckedSignalError(f"{where}: type must be one of {sorted(LINK_TYPES)}")
            label = str(ln.get("label") or "").strip()
            if not label or len(label) > LINK_LABEL_MAX:
                raise CheckedSignalError(f"{where}: label is required, at most {LINK_LABEL_MAX} characters")
            basis = ln.get("basis")
            if not isinstance(basis, dict):
                raise CheckedSignalError(f"{where}: basis must give the quote or link it rests on")
            quote = str(basis.get("quote") or "").strip()
            url = str(basis.get("url") or "").strip()
            if url and not _is_url(url):
                raise CheckedSignalError(f"{where}: basis.url must be an http(s) link")
            if kind in LINE_LINK_TYPES and not (quote and url):
                raise CheckedSignalError(f"{where}: an incident link needs both basis.quote and basis.url")
            if not (quote or url):
                raise CheckedSignalError(f"{where}: basis needs a quote or a link")
            if not str(ln.get("approved_by") or "").strip():
                raise CheckedSignalError(f"{where}: approved_by is required; links are approved by a person")
            _parse_iso(ln.get("approved_at"), "approved_at", where)
            key = (min(sid, to), max(sid, to), kind, label.lower())
            if key in pairs:
                raise CheckedSignalError(f"{where}: the same link is already recorded on {pairs[key][:8]}")
            pairs[key] = sid


def _reject_duplicates(signals: list[dict]) -> None:
    for key in ("signal_id", "external_id"):
        seen: set[str] = set()
        for s in signals:
            if s[key] in seen:
                raise CheckedSignalError(f"duplicate {key} {s[key]} across checked batches")
            seen.add(s[key])


# ── Database ──────────────────────────────────────────────────────────────────

UPSERT_SQL = """
INSERT INTO signals
    (signal_id, source, external_id, title, content, lat, lng, timestamp,
     status, metadata_json, is_priority, stream, relevance_score, source_type,
     gravity_score, processed_at, published_at, cluster_id, confidence_score)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1.0, ?, NULL, ?, ?, NULL, NULL)
ON CONFLICT(signal_id) DO UPDATE SET
    source=excluded.source, external_id=excluded.external_id, title=excluded.title,
    content=excluded.content, lat=excluded.lat, lng=excluded.lng,
    timestamp=excluded.timestamp, status=excluded.status,
    metadata_json=excluded.metadata_json, is_priority=0, stream=excluded.stream,
    relevance_score=1.0, source_type=excluded.source_type, gravity_score=NULL,
    processed_at=excluded.processed_at, published_at=excluded.published_at,
    cluster_id=NULL, confidence_score=NULL
"""

ROW_SQL = """
SELECT signal_id, source, external_id, title, content, lat, lng, timestamp, status,
       metadata_json, is_priority, stream, relevance_score, source_type,
       gravity_score, processed_at, published_at, cluster_id, confidence_score
FROM signals WHERE signal_id = ?
"""


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _name_rows(conn: sqlite3.Connection, sid: str) -> int:
    """signal_entities rows (the name finder's output) attached to a pin."""
    if not _has_table(conn, "signal_entities"):
        return 0
    return conn.execute("SELECT COUNT(*) FROM signal_entities WHERE signal_id = ?",
                        (sid,)).fetchone()[0]


def _row_dict(conn: sqlite3.Connection, sid: str) -> dict | None:
    cur = conn.execute(ROW_SQL, (sid,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([c[0] for c in cur.description], row))


def _desired_row(s: dict, retraction: dict | None) -> dict:
    meta = s["metadata"]
    if retraction:
        meta = dict(meta)
        meta["retracted"] = {"reason": retraction["reason"].strip(),
                             "retracted_at": retraction["retracted_at"]}
    return {
        "signal_id": s["signal_id"], "source": s["source"], "external_id": s["external_id"],
        "title": s["title"], "content": s["content"], "lat": float(s["lat"]),
        "lng": float(s["lng"]), "timestamp": s["timestamp"],
        "status": RETRACTED_STATUS if retraction else "reviewed",
        "metadata_json": json.dumps(meta, ensure_ascii=False),
        "is_priority": 0, "stream": s["stream"], "relevance_score": 1.0,
        "source_type": SOURCE_TYPE, "gravity_score": None,
        "processed_at": s["published_at"],
        "published_at": None if retraction else s["published_at"],
        # Machine traces: never part of a checked pin.
        "cluster_id": None, "confidence_score": None,
    }


def _record_from_row(row: dict) -> dict:
    try:
        meta = json.loads(row["metadata_json"] or "null")
    except (TypeError, ValueError):
        meta = None
    return {"signal_id": row["signal_id"], "external_id": row["external_id"],
            "source": row["source"], "title": row["title"], "content": row["content"],
            "lat": row["lat"], "lng": row["lng"], "stream": row["stream"],
            "timestamp": row["timestamp"], "published_at": row["published_at"],
            "metadata": meta}


@dataclass
class Action:
    kind: str          # insert | update | unchanged | withdraw | withdrawn
    signal_id: str
    title: str
    changed: list[str] = field(default_factory=list)

    def line(self) -> str:
        extra = f"  ({', '.join(self.changed)})" if self.changed else ""
        return f"{self.kind:<9} {self.signal_id[:8]}  {self.title}{extra}"


def plan(conn: sqlite3.Connection, cs: CheckedSet) -> list[Action]:
    """What an import would do, without writing."""
    retracted = cs.retracted
    actions = []
    for s in cs.signals:
        sid = s["signal_id"]
        clash = conn.execute("SELECT signal_id FROM signals WHERE external_id = ? AND signal_id != ?",
                             (s["external_id"], sid)).fetchone()
        if clash:
            raise CheckedSignalError(
                f"{sid}: external_id {s['external_id']} already belongs to signal {clash[0]} in the "
                f"database (probably a raw copy collected before the import). Resolve it by hand.")
        want = _desired_row(s, retracted.get(sid))
        have = _row_dict(conn, sid)
        if have is None:
            actions.append(Action("withdraw" if sid in retracted else "insert", sid, s["title"]))
            continue
        changed = [k for k, v in want.items() if have.get(k) != v]
        if k_meta_equal(have.get("metadata_json"), want["metadata_json"]) and "metadata_json" in changed:
            changed.remove("metadata_json")
        if _name_rows(conn, sid):
            changed.append("signal_entities")
        if not changed:
            kind = "withdrawn" if sid in retracted else "unchanged"
        else:
            kind = "withdraw" if sid in retracted else "update"
        actions.append(Action(kind, sid, s["title"], changed))
    return actions


def k_meta_equal(a: str | None, b: str | None) -> bool:
    """metadata_json compared as data, so key order or spacing is not a change."""
    try:
        return json.loads(a or "null") == json.loads(b or "null")
    except (TypeError, ValueError):
        return False


def import_checked(conn: sqlite3.Connection, cs: CheckedSet, write: bool = False) -> list[Action]:
    """Bring the database in line with the files. Dry run unless write=True.

    Every field is set on each write, retracted pins are withdrawn, and pins
    already in line are left untouched, so a second run changes nothing.
    """
    actions = plan(conn, cs)
    if not write:
        return actions
    retracted = cs.retracted
    by_id = {s["signal_id"]: s for s in cs.signals}
    try:
        for a in actions:
            if a.kind in ("unchanged", "withdrawn"):
                continue
            r = _desired_row(by_id[a.signal_id], retracted.get(a.signal_id))
            conn.execute(UPSERT_SQL, (
                r["signal_id"], r["source"], r["external_id"], r["title"], r["content"],
                r["lat"], r["lng"], r["timestamp"], r["status"], r["metadata_json"],
                r["stream"], r["source_type"], r["processed_at"], r["published_at"]))
            if "signal_entities" in a.changed:
                # Name-finder rows on a checked pin are machine traces: the
                # batch rules allow no entity links. Only this pin's rows go.
                conn.execute("DELETE FROM signal_entities WHERE signal_id = ?", (a.signal_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return actions


def verify(conn: sqlite3.Connection, cs: CheckedSet) -> list[str]:
    """The rebuild guard: every problem that should stop a build. Empty = safe."""
    problems = []
    retracted = cs.retracted
    for s in cs.signals:
        sid = s["signal_id"]
        row = _row_dict(conn, sid)
        short = f"{sid[:8]} ({s['title']})"
        if row is None:
            if sid not in retracted:
                problems.append(f"missing: {short}")
            continue
        if sid in retracted:
            if row["published_at"] is not None:
                problems.append(f"retracted but still published: {short}")
            continue
        if fingerprint(_record_from_row(row)) != s["fingerprint"]:
            problems.append(f"different from its checked file: {short}")
        want = _desired_row(s, None)
        for col in ("status", "source_type", "gravity_score", "processed_at",
                    "relevance_score", "cluster_id", "confidence_score"):
            if row[col] != want[col]:
                problems.append(f"{col} is {row[col]!r}, expected {want[col]!r}: {short}")
        n = _name_rows(conn, sid)
        if n:
            problems.append(f"{n} machine name row(s) in signal_entities: {short}")
    known = {s["signal_id"] for s in cs.signals}
    for (sid, title) in conn.execute(
            "SELECT signal_id, title FROM signals WHERE source_type = ? AND published_at IS NOT NULL",
            (SOURCE_TYPE,)):
        if sid not in known:
            problems.append(f"published checked pin with no checked file: {sid[:8]} ({title})")
    return problems


def count_markers_problems(index_html: pathlib.Path, cs: CheckedSet) -> list[str]:
    """After a build: the map must carry every active checked pin and no retracted one."""
    try:
        html = index_html.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{index_html} was not built"]
    m = re.search(r"var RAW = (\[.*?\]);\s*$", html, re.M)
    if not m:
        return ["could not find the map markers (var RAW) in index.html"]
    try:
        markers = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        return [f"map markers are not readable JSON ({exc})"]
    on_map = {mk.get("signal_id") for mk in markers if isinstance(mk, dict)}
    problems = []
    active = cs.active
    if len(markers) < len(active):
        problems.append(f"the map has {len(markers)} markers, fewer than the {len(active)} checked pins")
    for s in active:
        if s["signal_id"] not in on_map:
            problems.append(f"not on the map: {s['signal_id'][:8]} ({s['title']})")
    for sid in cs.retracted:
        if sid in on_map:
            problems.append(f"retracted pin still on the map: {sid[:8]}")
    return problems


# ── Stamping ──────────────────────────────────────────────────────────────────

def stamp_files(index_path: pathlib.Path = INDEX_PATH, check: bool = False) -> list[str]:
    """Recompute each pin's fingerprint. Returns the IDs whose stamp was stale."""
    idx = _read_json(index_path)
    stale = []
    for e in idx.get("batches", []):
        path = index_path.parent / e["file"]
        data = _read_json(path)
        file_stale = False
        for s in data.get("signals", []):
            fp = fingerprint(s)
            if s.get("fingerprint") != fp:
                stale.append(s.get("signal_id", "?"))
                s["fingerprint"] = fp
                file_stale = True
        if file_stale and not check:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stale


# ── CLI ───────────────────────────────────────────────────────────────────────

def _connect(db: pathlib.Path | None) -> sqlite3.Connection:
    """Open an existing database read-write. Never creates a file (mode=rw)."""
    if db is None:
        raise CheckedSignalError(f"no database to open: {_DB_PATH_ERROR or 'no path given'}")
    try:
        db = _paths.checked_path(db)
    except _paths.DBPathError as exc:
        raise CheckedSignalError(str(exc)) from None
    db = pathlib.Path(db)
    if not db.is_file():
        raise CheckedSignalError(f"{db} does not exist. Run: python app.py --init-db, then --migrate")
    try:
        conn = sqlite3.connect(_paths.sqlite_uri(db.absolute(), "rw"), uri=True, timeout=60)
    except sqlite3.OperationalError as exc:
        raise CheckedSignalError(f"{db} cannot be opened read-write: {exc}") from None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'").fetchone() is None:
        conn.close()
        raise CheckedSignalError(f"{db} has no signals table. Run: python app.py --init-db, then --migrate")
    return conn


def _approval_check(index: pathlib.Path, write: bool, out) -> None:
    """The pin approval ledger (tools/approvals.py) before anything is written.

    Problems with records that exist always refuse a write. Pins with no record
    refuse only once docs/approvals/pins/policy.json (or FORGE_APPROVALS_ENFORCE=1)
    turns enforcement on; until then they are reported, so current imports keep
    working while the legacy records are added. A dry run only reports.
    """
    from tools import approvals  # lazy: approvals imports this module
    report = approvals.verify(index_path=index)
    for line in approvals.report_lines(report):
        out(("" if write else "[dry run] ") + line)
    if write and not report.ok:
        raise CheckedSignalError("pin approval ledger refused the import: "
                                 + approvals.refusal_reason(report))


def run_import(db: pathlib.Path | None = DB_PATH, index: pathlib.Path = INDEX_PATH,
               write: bool = False, out=print) -> list[Action]:
    if write and _paths.is_staging():
        raise CheckedSignalError(
            "import --write refused: FORGE_ROLE=staging never imports hand-checked pins; "
            "they reach the authority only from the agent's own account")
    cs = load_index(index)
    _approval_check(index, write, out)
    conn = _connect(db)
    try:
        actions = import_checked(conn, cs, write=write)
    finally:
        conn.close()
    prefix = "" if write else "[dry run] "
    for a in actions:
        if a.kind not in ("unchanged", "withdrawn"):
            out(prefix + a.line())
    counts: dict[str, int] = {}
    for a in actions:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    out(f"{prefix}{len(cs.batches)} batches, {len(cs.signals)} pins: {summary}")
    if not write and any(a.kind not in ("unchanged", "withdrawn") for a in actions):
        out("Nothing written. Re-run with --write to apply.")
    return actions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("import", "verify", "stamp"):
        p = sub.add_parser(name)
        p.add_argument("--index", type=pathlib.Path, default=INDEX_PATH)
        if name != "stamp":
            p.add_argument("--db", type=pathlib.Path, default=DB_PATH)
        if name == "import":
            p.add_argument("--write", action="store_true", help="apply (default is a dry run)")
        if name == "stamp":
            p.add_argument("--check", action="store_true", help="report stale fingerprints, change nothing")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "import":
            run_import(args.db, args.index, args.write)
            return 0
        if args.cmd == "verify":
            cs = load_index(args.index)
            conn = _connect(args.db)
            try:
                problems = verify(conn, cs)
            finally:
                conn.close()
            for p in problems:
                print("[checked] " + p)
            print(f"[checked] {len(cs.active)} active pins, {len(cs.retracted)} retracted: "
                  + ("OK" if not problems else f"{len(problems)} problem(s)"))
            return 0 if not problems else 1
        stale = stamp_files(args.index, check=args.check)
        for sid in stale:
            print(("stale " if args.check else "stamped ") + sid)
        print(f"{len(stale)} fingerprint(s) {'stale' if args.check else 'updated'}")
        return 1 if (args.check and stale) else 0
    except CheckedSignalError as exc:
        print(f"[checked] refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

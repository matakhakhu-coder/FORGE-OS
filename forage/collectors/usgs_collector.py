#!/usr/bin/env python3
"""
FORAGE — USGS Earthquake Signal Collector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingests earthquake events from the USGS Earthquake Hazards Programme
public GeoJSON Summary API and stores them in the FORGE signals table.

Feed:   USGS Earthquake Hazards Programme — GeoJSON Summary Feed
URL:    https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/
Docs:   https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php

Feed variants (set FORAGE_USGS_FEED env var to override):
  significant_hour  — M2.5+ past hour
  significant_day   — significant events past 24 h  (DEFAULT)
  significant_week  — significant events past 7 days
  all_hour          — all events past hour
  all_day           — all events past 24 h
  2.5_day           — M2.5+ past 24 h
  4.5_day           — M4.5+ past 24 h   ← good for geopolitical context
  2.5_week          — M2.5+ past 7 days

Design decisions
────────────────
• Idempotent:  USGS feature IDs (e.g. "us7000abc1") are used as
  external_id.  INSERT OR IGNORE means re-runs never duplicate.

• Priority detection: magnitude threshold (default ≥ 6.0) OR keyword
  match on place/title text (tsunami, alert, red).  Both can be tuned
  via env vars without editing code.

• Coordinates: every USGS GeoJSON feature carries [lng, lat, depth].
  Depth is stored in metadata_json; lat/lng go into the signals columns
  so the map layer picks them up automatically with zero extra work.

• source = "usgs" (lowercase) — matches the source-badge CSS already
  present in signals.html and the map popup colour logic.

• metadata_json: stores magnitude, place, depth, tsunami flag, alert
  level, felt count, and the USGS detail URL verbatim.

• Zero dependencies: only Python stdlib (urllib, json).

Usage
─────
    python forage/collectors/usgs_collector.py
    python forage/collectors/usgs_collector.py --feed 4.5_day
    python forage/collectors/usgs_collector.py --min-mag 5.0
    python forage/collectors/usgs_collector.py --dry-run
    python forage/collectors/usgs_collector.py --db /path/to/database.db

Cron (every 30 minutes):
    */30 * * * * cd /path/to/FORGE && \\
        python forage/collectors/usgs_collector.py >> logs/usgs.log 2>&1

Priority thresholds (env overrides):
    FORAGE_USGS_MIN_MAG     — minimum magnitude to ingest   (default 2.5)
    FORAGE_USGS_PRIORITY_MAG — magnitude threshold for is_priority=1  (default 6.0)
    FORAGE_USGS_FEED        — feed variant key (default significant_day)
"""

import argparse
import json
import time as _time_mod
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
# Phase 32: path-safe pipeline logger import
def _log_run_safe(*args, **kwargs):
    """Inline log_run that works whether called as a module or direct script."""
    import sys as _sys, importlib.util as _ilu
    from pathlib import Path as _P
    _logger_path = _P(__file__).resolve().parent.parent.parent / "forage" / "utils" / "pipeline_logger.py"
    if str(_logger_path.parent.parent) not in _sys.path:
        _sys.path.insert(0, str(_logger_path.parent.parent))
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_logger_path))
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass  # logging must never crash the pipeline
log_run = _log_run_safe
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Feed configuration ────────────────────────────────────────────────────────

_FEED_BASE = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/"

FEED_VARIANTS = {
    "significant_hour": "significant_hour.geojson",
    "significant_day":  "significant_day.geojson",
    "significant_week": "significant_week.geojson",
    "all_hour":         "all_hour.geojson",
    "all_day":          "all_day.geojson",
    "2.5_day":          "2.5_day.geojson",
    "2.5_week":         "2.5_week.geojson",
    "4.5_day":          "4.5_day.geojson",
    "4.5_week":         "4.5_week.geojson",
}

DEFAULT_FEED     = os.environ.get("FORAGE_USGS_FEED", "2.5_day")
MIN_MAG          = float(os.environ.get("FORAGE_USGS_MIN_MAG", "2.5"))
PRIORITY_MAG     = float(os.environ.get("FORAGE_USGS_PRIORITY_MAG", "6.0"))
SOURCE_TAG       = "usgs"
TIMEOUT_SEC      = 25

# ── Priority keywords (title / place text, case-insensitive) ──────────────────

PRIORITY_KEYWORDS = [
    "tsunami", "red alert", "orange alert", "nuclear",
    "felt widely", "major damage",
]

# ── Database path resolution ──────────────────────────────────────────────────

def _resolve_db(override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    # collector lives at forage/collectors/usgs_collector.py → up 3 levels
    return Path(__file__).resolve().parent.parent.parent / "database.db"

# ── Logging helpers ───────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [usgs_collector] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [usgs_collector] WARN  {msg}", file=sys.stderr, flush=True)
def err(msg: str)  -> None: print(f"[{_ts()}] [usgs_collector] ERROR {msg}", file=sys.stderr, flush=True)

# ── Feed fetch ────────────────────────────────────────────────────────────────

def _feed_url(variant: str) -> str:
    filename = FEED_VARIANTS.get(variant)
    if not filename:
        raise ValueError(
            f"Unknown feed variant '{variant}'. "
            f"Valid options: {', '.join(FEED_VARIANTS)}"
        )
    return _FEED_BASE + filename


def fetch_feed(variant: str = DEFAULT_FEED) -> dict:
    """Download USGS GeoJSON feed and return parsed dict."""
    url = _feed_url(variant)
    log(f"Fetching: {url}")
    headers = {
        "User-Agent": "FORGE-OS/1.0 FORAGE-USGSCollector (+local)",
        "Accept":     "application/json",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} fetching USGS feed: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error fetching USGS feed: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse USGS JSON: {exc}") from exc

# ── Feature → signal ──────────────────────────────────────────────────────────

def _ms_to_iso(ms: int | None) -> str:
    """Convert USGS epoch-milliseconds timestamp to ISO datetime string."""
    if ms is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _check_priority(mag: float | None, title: str, place: str) -> int:
    """Return 1 if magnitude threshold met OR a priority keyword matches."""
    if mag is not None and mag >= PRIORITY_MAG:
        return 1
    combined = (title + " " + place).lower()
    for kw in PRIORITY_KEYWORDS:
        if kw in combined:
            return 1
    return 0


def feature_to_signal(feature: dict, min_mag: float = MIN_MAG) -> dict | None:
    """
    Convert a single USGS GeoJSON Feature into a FORGE signal dict.
    Returns None if the feature should be skipped (below min_mag, malformed).
    """
    try:
        props  = feature.get("properties") or {}
        geom   = feature.get("geometry")   or {}
        fid    = feature.get("id", "")

        if not fid:
            warn("Feature missing 'id' — skipping")
            return None

        # Coordinates: GeoJSON is [longitude, latitude, depth_km]
        coords = geom.get("coordinates") or []
        lng    = float(coords[0]) if len(coords) > 0 else None
        lat    = float(coords[1]) if len(coords) > 1 else None
        depth  = float(coords[2]) if len(coords) > 2 else None

        mag    = props.get("mag")
        if mag is not None:
            try:
                mag = float(mag)
            except (TypeError, ValueError):
                mag = None

        # Filter below minimum magnitude
        if mag is not None and mag < min_mag:
            return None

        place  = (props.get("place") or "Unknown location").strip()
        ts     = _ms_to_iso(props.get("time"))

        # Build human-readable title
        if mag is not None:
            title = f"M{mag:.1f} — {place}"
        else:
            title = f"Earthquake — {place}"

        # Content / description
        parts = []
        if mag is not None:
            parts.append(f"Magnitude {mag:.1f}")
        if depth is not None:
            parts.append(f"depth {depth:.1f} km")
        if place:
            parts.append(place)
        alert = props.get("alert")
        if alert:
            parts.append(f"USGS alert: {alert.upper()}")
        tsunami = props.get("tsunami")
        if tsunami:
            parts.append("⚠ TSUNAMI WARNING ISSUED")
        content = " · ".join(parts)

        # Priority
        priority = _check_priority(mag, title, place)
        if tsunami:
            priority = 1  # always flag tsunami events

        # Metadata — store everything useful for future analysis
        meta = {
            "mag":        mag,
            "place":      place,
            "depth_km":   depth,
            "alert":      alert,
            "tsunami":    tsunami,
            "felt":       props.get("felt"),
            "cdi":        props.get("cdi"),    # Community Internet Intensity
            "mmi":        props.get("mmi"),    # Modified Mercalli Intensity
            "sig":        props.get("sig"),    # USGS significance score
            "magType":    props.get("magType"),
            "type":       props.get("type"),
            "status":     props.get("status"),
            "net":        props.get("net"),
            "code":       props.get("code"),
            "detail_url": props.get("detail"),
            "url":        props.get("url"),
            "updated":    _ms_to_iso(props.get("updated")),
        }
        # Scrub None values to keep JSON clean
        meta = {k: v for k, v in meta.items() if v is not None}

        return {
            "signal_id":     str(uuid.uuid4()),
            "source":        SOURCE_TAG,
            "external_id":   f"usgs:{fid}",
            "title":         title[:255],
            "content":       content[:1000],
            "lat":           lat,
            "lng":           lng,
            "timestamp":     ts,
            "status":        "raw",
            "metadata_json": json.dumps(meta, ensure_ascii=False),
            "is_priority":   priority,
        }

    except Exception as exc:
        warn(f"Skipping malformed feature '{feature.get('id', '?')}': {exc}")
        return None

# ── Database operations ───────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run:  python app.py --init-db\n"
            "Or set FORGE_DB=/path/to/database.db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_signals_table(conn: sqlite3.Connection) -> None:
    """
    Create / migrate the signals table so the collector works stand-alone
    before `python app.py --init-db` has been run.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            signal_id     TEXT    PRIMARY KEY,
            source        TEXT    NOT NULL,
            external_id   TEXT    NOT NULL UNIQUE,
            title         TEXT    NOT NULL,
            content       TEXT,
            lat           REAL,
            lng           REAL,
            timestamp     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status        TEXT    NOT NULL DEFAULT 'raw'
                          CHECK(status IN ('raw','reviewed','promoted','dismissed')),
            metadata_json TEXT,
            cluster_id    TEXT,
            is_priority   INTEGER NOT NULL DEFAULT 0
        )
    """)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    for col, defn in [
        ("cluster_id",  "TEXT"),
        ("is_priority", "INTEGER NOT NULL DEFAULT 0"),
        ("source",      "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {defn}")
    conn.commit()


def insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> tuple[int, int]:
    """
    Bulk-insert via INSERT OR IGNORE.
    Returns (inserted_count, skipped_count).
    """
    inserted = skipped = 0
    for sig in signals:
        cur = conn.execute("""
            INSERT OR IGNORE INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, metadata_json, is_priority)
            VALUES
                (:signal_id, :source, :external_id, :title, :content,
                 :lat, :lng, :timestamp, :status, :metadata_json, :is_priority)
        """, sig)
        if cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped

# ── CLI entry point ───────────────────────────────────────────────────────────

def run(feed: str = DEFAULT_FEED, min_mag: float = MIN_MAG,
        db_path: Path | None = None, dry_run: bool = False) -> int:
    """Execute one collection cycle. Returns exit code (0 = success, 1 = error)."""

    resolved_db = _resolve_db(str(db_path) if db_path else None)

    _t0 = _time_mod.monotonic()
    log(f"Feed variant : {feed}")
    log(f"Min magnitude: M{min_mag:.1f}")
    log(f"Priority mag : M{PRIORITY_MAG:.1f}")
    log(f"Database     : {resolved_db}")
    if dry_run:
        log("DRY RUN — no writes will occur")

    try:
        data = fetch_feed(feed)
    except (RuntimeError, ValueError) as exc:
        err(str(exc))
        return 1

    features = data.get("features") or []
    meta_info = data.get("metadata") or {}
    log(f"Feed reports {meta_info.get('count', '?')} features / "
        f"{len(features)} in payload")

    signals = [
        s for f in features
        if (s := feature_to_signal(f, min_mag=min_mag)) is not None
    ]
    priority_count = sum(1 for s in signals if s["is_priority"])

    log(f"Mapped {len(signals)}/{len(features)} features → signals "
        f"({priority_count} priority)")

    if dry_run:
        for s in signals[:5]:
            log(f"  [DRY] {s['title']} | lat={s['lat']} lng={s['lng']} "
                f"priority={s['is_priority']}")
        if len(signals) > 5:
            log(f"  [DRY] … and {len(signals)-5} more")
        log("Dry run complete — no database writes.")
        return 0

    try:
        conn = _open_db(resolved_db)
    except FileNotFoundError as exc:
        err(str(exc))
        return 1

    try:
        _ensure_signals_table(conn)
        inserted, skipped = insert_signals(conn, signals)
    finally:
        conn.close()

    log(f"Done — inserted: {inserted}, skipped (duplicate): {skipped}")
    if inserted > 0 and priority_count > 0:
        log("⚠  PRIORITY SIGNALS INSERTED — check Signal Monitor at /signals")
    log_run(resolved_db, "usgs_collector", "success",
            records_in=len(signals), records_out=inserted,
            duration_s=_time_mod.monotonic() - _t0)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORAGE USGS Earthquake Collector — ingests USGS GeoJSON feed into FORGE signals table"
    )
    parser.add_argument(
        "--feed", default=DEFAULT_FEED,
        choices=list(FEED_VARIANTS.keys()),
        help=f"USGS feed variant (default: {DEFAULT_FEED})"
    )
    parser.add_argument(
        "--min-mag", type=float, default=MIN_MAG,
        dest="min_mag",
        help=f"Minimum magnitude to ingest (default: {MIN_MAG})"
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Override path to database.db"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse feed but do not write to database"
    )
    args = parser.parse_args()
    sys.exit(run(
        feed=args.feed,
        min_mag=args.min_mag,
        db_path=args.db,
        dry_run=args.dry_run,
    ))
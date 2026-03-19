#!/usr/bin/env python3
"""
FORAGE — Earthquake Signal Collector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches seismic events from the USGS Earthquake Hazards Program public feed
and stores them in the FORGE signals table for analyst review.

Feed:   USGS GeoJSON Summary — Past Hour, Magnitude ≥ 2.5
URL:    https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_hour.geojson
Docs:   https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php

Design decisions
────────────────
• Idempotent: each USGS event has a stable `id` field (e.g. "us7000lbcd").
  The signals table has a UNIQUE constraint on `external_id`, so inserting the
  same event twice is a harmless no-op via INSERT OR IGNORE.

• Title format:  "M{mag} — {place}"   (e.g. "M3.4 — 12 km NE of Ridgecrest, CA")
  This is both human-readable and sortable by magnitude prefix.

• Content field: prose sentence with magnitude, depth, and place extracted from
  the GeoJSON properties, so analysts have enough context without opening a
  second window.

• metadata_json: the full USGS properties dict is serialised and stored verbatim
  so no signal intelligence is lost.  Analysts can inspect `cdi`, `mmi`,
  `alert`, `tsunami`, `sig`, `felt`, and other USGS-specific fields.

• status defaults to 'raw' — the analyst promotes or dismisses from /signals.

Usage
─────
    # Run once manually
    python forage/collectors/earthquake_collector.py

    # Schedule with cron (every 5 minutes)
    */5 * * * * cd /path/to/FORGE && python forage/collectors/earthquake_collector.py >> logs/forage.log 2>&1

Environment
───────────
The script resolves the database path relative to its own file location,
climbing up two directories to reach the FORGE root where database.db lives.
Override with:   FORGE_DB=/custom/path/to/database.db python earthquake_collector.py
"""

import json
import time
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
from urllib.request import urlopen, Request

# ── Feed configuration ────────────────────────────────────────────────────────

FEED_URL    = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_hour.geojson"
SOURCE_TAG  = "USGS"
TIMEOUT_SEC = 20

# ── Database path resolution ──────────────────────────────────────────────────

def _resolve_db() -> Path:
    """
    Walk up from this file's location to find database.db.

    Expected layout:
        FORGE/
            database.db           ← target
            forage/
                collectors/
                    earthquake_collector.py  ← this file
    """
    env_override = os.environ.get("FORGE_DB")
    if env_override:
        return Path(env_override).resolve()

    here    = Path(__file__).resolve()
    # Two levels up: collectors/ → forage/ → FORGE/
    db_path = here.parent.parent.parent / "database.db"
    return db_path

DB_PATH = _resolve_db()

# ── Logging helpers ───────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str) -> None:
    print(f"[{_ts()}] [earthquake_collector] {msg}", flush=True)

def warn(msg: str) -> None:
    print(f"[{_ts()}] [earthquake_collector] WARN  {msg}", file=sys.stderr, flush=True)

def err(msg: str) -> None:
    print(f"[{_ts()}] [earthquake_collector] ERROR {msg}", file=sys.stderr, flush=True)

# ── USGS feed fetch ───────────────────────────────────────────────────────────

def fetch_feed(url: str = FEED_URL) -> dict:
    """
    Download the USGS GeoJSON feed.  Returns parsed dict.
    Raises RuntimeError on HTTP or network failure.
    """
    headers = {
        "User-Agent": "FORGE-OS/1.0 FORAGE-EarthquakeCollector (+local)",
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
        raise RuntimeError(f"Failed to parse USGS response as JSON: {exc}") from exc

# ── Record mapping ────────────────────────────────────────────────────────────

def _format_magnitude(mag) -> str:
    """Return 'M{mag}' with one decimal place, or 'M?' if not available."""
    if mag is None:
        return "M?"
    try:
        return f"M{float(mag):.1f}"
    except (TypeError, ValueError):
        return "M?"

def _build_title(props: dict) -> str:
    """
    'M3.4 — 12 km NE of Ridgecrest, CA'
    Falls back gracefully if fields are absent.
    """
    mag_str = _format_magnitude(props.get("mag"))
    place   = (props.get("place") or "unknown location").strip()
    return f"{mag_str} — {place}"

def _build_content(props: dict) -> str:
    """
    One-line prose summary for the content field.
    Example:
        Magnitude 3.4 earthquake near 12 km NE of Ridgecrest, CA.
        Depth: 8.2 km. Status: reviewed.
    """
    mag       = props.get("mag")
    place     = (props.get("place") or "unknown location").strip()
    depth_km  = props.get("depth")
    status    = props.get("status", "automatic")
    mag_type  = props.get("magType", "")

    parts = []

    mag_label = f"Magnitude {float(mag):.1f}" if mag is not None else "Magnitude unknown"
    mag_label += f" ({mag_type})" if mag_type else ""
    parts.append(f"{mag_label} earthquake near {place}.")

    if depth_km is not None:
        try:
            parts.append(f"Depth: {float(depth_km):.1f} km.")
        except (TypeError, ValueError):
            pass

    if status:
        parts.append(f"Status: {status}.")

    # Felt reports and significance score
    felt = props.get("felt")
    sig  = props.get("sig")
    if felt:
        parts.append(f"Felt reports: {int(felt)}.")
    if sig:
        parts.append(f"Significance: {int(sig)}.")

    return " ".join(parts)

def feature_to_signal(feature: dict) -> dict | None:
    """
    Convert a single GeoJSON feature from the USGS feed into a FORGE
    signal record dict.  Returns None if the feature is malformed.
    """
    try:
        external_id = feature["id"]
        props       = feature.get("properties") or {}
        geometry    = feature.get("geometry")  or {}
        coords      = geometry.get("coordinates") or []

        # Coordinates are [longitude, latitude, depth]
        lng = float(coords[0]) if len(coords) > 0 else None
        lat = float(coords[1]) if len(coords) > 1 else None

        # USGS `time` is epoch-milliseconds UTC
        epoch_ms  = props.get("time")
        if epoch_ms:
            epoch_sec = int(epoch_ms) / 1000.0
            ts = datetime.fromtimestamp(epoch_sec, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "signal_id":     str(uuid.uuid4()),
            "source":        SOURCE_TAG,
            "external_id":   external_id,
            "title":         _build_title(props),
            "content":       _build_content(props),
            "lat":           lat,
            "lng":           lng,
            "timestamp":     ts,
            "status":        "raw",
            "metadata_json": json.dumps(props, ensure_ascii=False),
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        warn(f"Skipping malformed feature ({feature.get('id', '?')}): {exc}")
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
    Create the signals table if it doesn't exist yet.
    This mirrors the SCHEMA_STATEMENTS entry in app.py so the collector
    can be run before `python app.py --migrate` without errors.
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
            metadata_json TEXT
        )
    """)
    conn.commit()

def insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> tuple[int, int]:
    """
    Bulk-insert signal records.

    Uses INSERT OR IGNORE so duplicate external_ids are silently skipped —
    running the collector repeatedly never produces duplicates.

    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped  = 0

    for sig in signals:
        cur = conn.execute("""
            INSERT OR IGNORE INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, metadata_json)
            VALUES
                (:signal_id, :source, :external_id, :title, :content,
                 :lat, :lng, :timestamp, :status, :metadata_json)
        """, sig)
        if cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    return inserted, skipped

# ── Main entry point ──────────────────────────────────────────────────────────

def run() -> int:
    """
    Execute one collection cycle.  Returns exit code (0 = success, 1 = error).
    """
    _t0 = time.monotonic()
    log(f"Starting collection cycle — feed: {FEED_URL}")
    log(f"Database: {DB_PATH}")

    # 1. Fetch
    try:
        data = fetch_feed()
    except RuntimeError as exc:
        err(str(exc))
        return 1

    features = data.get("features") or []
    log(f"Feed contains {len(features)} features "
        f"(bbox count: {data.get('metadata', {}).get('count', '?')})")

    if not features:
        log("No features in feed — nothing to do.")
        return 0

    # 2. Map features → signal records
    signals = [s for f in features if (s := feature_to_signal(f)) is not None]
    log(f"Mapped {len(signals)}/{len(features)} features to signal records")

    # 3. Connect and insert
    try:
        conn = _open_db(DB_PATH)
    except FileNotFoundError as exc:
        err(str(exc))
        return 1

    try:
        _ensure_signals_table(conn)
        inserted, skipped = insert_signals(conn, signals)
    finally:
        conn.close()

    log(f"Done — inserted: {inserted}, skipped (already present): {skipped}")
    log_run(DB_PATH, "earthquake_collector", "success",
            records_in=len(signals), records_out=inserted,
            duration_s=time.monotonic() - _t0)
    return 0

if __name__ == "__main__":
    sys.exit(run())

# --- MEGA RUNNER ADAPTER ---
import asyncio as _asyncio

async def async_main(**kwargs):
    try:
        result = run()
        if _asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[ERROR] async_main failed in earthquake_collector.py: {e}")
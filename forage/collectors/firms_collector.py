#!/usr/bin/env python3
"""
FORAGE — NASA FIRMS Fire Signal Collector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingests active fire / thermal anomaly data from NASA FIRMS
(Fire Information for Resource Management System) and stores
detections as FORGE signals.

Feed:   NASA FIRMS MODIS/VIIRS Active Fire CSV feeds
URL:    https://firms.modaps.eosdis.nasa.gov/data/active_fire/
Docs:   https://firms.modaps.eosdis.nasa.gov/descriptions/FIRMS_MODIS_V6.1.pdf

Feed variants (set FORAGE_FIRMS_FEED env var to override):
  MODIS_NRT — MODIS Near Real-Time,  1km resolution  (DEFAULT)
  VIIRS_NOAA20_NRT — VIIRS NOAA-20, 375m resolution  (higher detail)
  VIIRS_SNPP_NRT   — VIIRS S-NPP,   375m resolution

Time window (set FORAGE_FIRMS_DAYS env var, default 1):
  1   — last 24 hours   ← default (most current, smallest payload)
  2   — last 48 hours
  7   — last 7 days

IMPORTANT — No API key required:
  NASA FIRMS provides public CSV feeds with no registration.
  The URL pattern is:
    https://firms.modaps.eosdis.nasa.gov/data/active_fire/
      {instrument}/csv/{instrument_code}_{region}_{days}d.csv

  Default feed used:
    https://firms.modaps.eosdis.nasa.gov/data/active_fire/
      modis-c6.1/csv/MODIS_C6_1_Global_24h.csv

Design decisions
────────────────
• external_id: "firms:{latitude}:{longitude}:{acq_date}:{acq_time}"
  guarantees idempotency — the same detection won't be double-inserted.

• Priority: FRP (Fire Radiative Power) ≥ 100 MW → is_priority=1.
  Confidence field from FIRMS CSV also considered (high/nominal/low).

• source = "firms" (lowercase) — follow same convention as "usgs".

• Coordinates: FIRMS CSV gives lat/lon directly — map directly to
  signals.lat / signals.lng for immediate compatibility with the map.

• metadata_json: stores brightness, frp, confidence, satellite,
  daynight, scan, track verbatim for downstream analysis.

• Confidence scoring: applied via the same heuristic as ner_processor
  so the confidence_score column is populated on insert.

Usage
─────
    python forage/collectors/firms_collector.py
    python forage/collectors/firms_collector.py --feed VIIRS_NOAA20_NRT
    python forage/collectors/firms_collector.py --days 2
    python forage/collectors/firms_collector.py --min-frp 50
    python forage/collectors/firms_collector.py --dry-run

Cron (every hour — FIRMS updates roughly hourly):
    0 * * * * cd /path/to/FORGE && \\
        python forage/collectors/firms_collector.py >> logs/firms.log 2>&1

Environment variables:
    FORAGE_FIRMS_FEED   — feed variant key (default MODIS_NRT)
    FORAGE_FIRMS_DAYS   — time window in days (default 1)
    FORAGE_FIRMS_MIN_FRP — minimum FRP (MW) to ingest (default 0 — all)
    FORGE_DB            — override database path
"""

import argparse
import csv
import io
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
from urllib.request import Request, urlopen

# ── Feed configuration ────────────────────────────────────────────────────────

# Base URL for FIRMS public CSV feeds (no API key required)
_FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"

# Feed variant → (path_segment, csv_prefix)
FEED_VARIANTS = {
    "MODIS_NRT":       ("modis-c6.1/csv",       "MODIS_C6_1_Global"),
    "VIIRS_NOAA20_NRT":("viirs-i-noaa20-nrt/csv","J1_VIIRS_C2_Global"),
    "VIIRS_SNPP_NRT":  ("viirs-i-snpp-nrt/csv",  "VNP14IMGTDL_NRT_Global"),
}

DEFAULT_FEED    = os.environ.get("FORAGE_FIRMS_FEED", "MODIS_NRT")
DEFAULT_DAYS    = int(os.environ.get("FORAGE_FIRMS_DAYS", "1"))
MIN_FRP         = float(os.environ.get("FORAGE_FIRMS_MIN_FRP", "0"))
PRIORITY_FRP    = 100.0   # MW — fires above this are flagged as priority
SOURCE_TAG      = "firms"
TIMEOUT_SEC     = 40       # FIRMS CSVs can be large

# ── DB path resolution ────────────────────────────────────────────────────────

def _resolve_db(override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [firms_collector] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [firms_collector] WARN  {msg}", file=sys.stderr, flush=True)
def err(msg: str)  -> None: print(f"[{_ts()}] [firms_collector] ERROR {msg}", file=sys.stderr, flush=True)

# ── Confidence scoring (mirrors ner_processor heuristic) ─────────────────────

_CONF_WEIGHTS = [
    ("confirmed", 0.3), ("official", 0.3), ("verified", 0.3),
    ("breaking",  0.2), ("urgent",   0.2),
    ("fire",      0.1), ("wildfire", 0.1), ("alert",    0.1),
]
_BASE_CONF = 0.2

def _confidence(title: str, content: str) -> float:
    combined = (title + " " + content).lower()
    score = _BASE_CONF
    for kw, w in _CONF_WEIGHTS:
        if kw in combined:
            score += w
    return min(round(score, 3), 1.0)

# ── Feed URL builder ──────────────────────────────────────────────────────────

def _feed_url(variant: str, days: int) -> str:
    info = FEED_VARIANTS.get(variant)
    if not info:
        raise ValueError(
            f"Unknown FIRMS feed variant '{variant}'. "
            f"Valid options: {', '.join(FEED_VARIANTS)}"
        )
    path_seg, prefix = info
    # Days suffix: 24h → "24h", 48h → "48h", 7d → "7d"
    # FIRMS actually uses: 24h.csv / 48h.csv / 7d.csv
    if days == 1:
        suffix = "24h"
    elif days == 2:
        suffix = "48h"
    else:
        suffix = f"{days}d"
    filename = f"{prefix}_{suffix}.csv"
    return f"{_FIRMS_BASE}{path_seg}/{filename}"

# ── CSV fetch ─────────────────────────────────────────────────────────────────

def fetch_csv(url: str) -> list[dict]:
    """
    Fetch FIRMS CSV and return list of row dicts.
    FIRMS CSV has a header row; columns vary by feed variant but always
    include: latitude, longitude, acq_date, acq_time, confidence, frp.
    """
    log(f"Fetching: {url}")
    headers = {
        "User-Agent": "FORGE-OS/1.0 FORAGE-FIRMSCollector (+local)",
        "Accept":     "text/csv, text/plain",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} fetching FIRMS: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error fetching FIRMS: {exc.reason}") from exc

    reader = csv.DictReader(io.StringIO(raw))
    return list(reader)

# ── Row → signal ──────────────────────────────────────────────────────────────

def _safe_float(val: str | None, default: float | None = None) -> float | None:
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def row_to_signal(row: dict, min_frp: float = MIN_FRP) -> dict | None:
    """
    Convert a single FIRMS CSV row into a FORGE signal dict.
    Returns None if the row should be skipped.

    FIRMS MODIS columns (lowercase normalised):
      latitude, longitude, brightness, scan, track, acq_date, acq_time,
      satellite, instrument, confidence, version, bright_t31, frp, daynight
    VIIRS columns are similar but include bright_ti4, bright_ti5.
    We handle both by lowercase key normalisation.
    """
    try:
        # Normalise column names to lowercase stripped
        r = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items()}

        lat = _safe_float(r.get("latitude"))
        lng = _safe_float(r.get("longitude"))
        if lat is None or lng is None:
            warn(f"Row missing coordinates — skipping: {dict(list(r.items())[:3])}")
            return None

        frp        = _safe_float(r.get("frp"), 0.0)
        brightness = _safe_float(r.get("brightness") or r.get("bright_ti4"))
        confidence = r.get("confidence", "").strip().lower()  # 'high'/'nominal'/'low' or 0-100
        acq_date   = r.get("acq_date", "")      # YYYY-MM-DD
        acq_time   = r.get("acq_time", "")      # HHMM
        satellite  = r.get("satellite", r.get("instrument", ""))
        daynight   = r.get("daynight", "")

        # Filter below minimum FRP
        if frp is not None and frp < min_frp:
            return None

        # Build timestamp
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if acq_date and len(acq_date) == 10:
            try:
                if acq_time and len(acq_time) >= 3:
                    t_str = acq_time.zfill(4)
                    ts = f"{acq_date} {t_str[:2]}:{t_str[2:4]}:00"
                else:
                    ts = f"{acq_date} 00:00:00"
            except Exception:
                pass

        # Human-readable title
        frp_str = f"{frp:.0f} MW" if frp else "unknown FRP"
        dn_str  = {"D": "daytime", "N": "night-time"}.get(daynight.upper(), "")
        title   = f"🔥 Active Fire — {lat:.3f}°, {lng:.3f}° [{frp_str}]"
        if dn_str:
            title += f" ({dn_str})"

        # Content
        parts = [f"FRP: {frp_str}"]
        if brightness:
            parts.append(f"Brightness: {brightness:.1f} K")
        if confidence:
            parts.append(f"Confidence: {confidence}")
        if satellite:
            parts.append(f"Satellite: {satellite}")
        content = " · ".join(parts)

        # Priority: high FRP or high confidence
        is_priority = 0
        if frp and frp >= PRIORITY_FRP:
            is_priority = 1
        if confidence in ("high", "h"):
            is_priority = 1

        # Stable external_id: prevents duplicates across re-runs
        # FIRMS can update confidence scores, so we key on position+time
        ext_id = f"firms:{lat:.4f}:{lng:.4f}:{acq_date}:{acq_time}"

        meta = {
            "frp":          frp,
            "brightness":   brightness,
            "confidence":   confidence,
            "satellite":    satellite,
            "daynight":     daynight,
            "acq_date":     acq_date,
            "acq_time":     acq_time,
            "scan":         _safe_float(r.get("scan")),
            "track":        _safe_float(r.get("track")),
        }
        meta = {k: v for k, v in meta.items() if v is not None and v != ""}

        conf_score = _confidence(title, content)

        return {
            "signal_id":        str(uuid.uuid4()),
            "source":           SOURCE_TAG,
            "external_id":      ext_id,
            "title":            title[:255],
            "content":          content[:1000],
            "lat":              lat,
            "lng":              lng,
            "timestamp":        ts,
            "status":           "raw",
            "metadata_json":    json.dumps(meta, ensure_ascii=False),
            "is_priority":      is_priority,
            "confidence_score": conf_score,
        }

    except Exception as exc:
        warn(f"Skipping malformed FIRMS row: {exc}")
        return None

# ── DB operations ─────────────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run:  python app.py --init-db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_signals_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            signal_id       TEXT    PRIMARY KEY,
            source          TEXT    NOT NULL,
            external_id     TEXT    NOT NULL UNIQUE,
            title           TEXT    NOT NULL,
            content         TEXT,
            lat             REAL,
            lng             REAL,
            timestamp       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status          TEXT    NOT NULL DEFAULT 'raw'
                            CHECK(status IN ('raw','reviewed','promoted','dismissed')),
            metadata_json   TEXT,
            cluster_id      TEXT,
            is_priority     INTEGER NOT NULL DEFAULT 0
        )
    """)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    for col, defn in [
        ("cluster_id",       "TEXT"),
        ("is_priority",      "INTEGER NOT NULL DEFAULT 0"),
        ("source",           "TEXT"),
        ("confidence_score", "REAL"),     # Phase 18
    ]:
        if col not in existing:
            log(f"Adding column signals.{col}…")
            conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {defn}")
    conn.commit()


def insert_signals(conn: sqlite3.Connection,
                   signals: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    for sig in signals:
        # confidence_score may not exist in older schema rows — handle gracefully
        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, metadata_json,
                     is_priority, confidence_score)
                VALUES
                    (:signal_id, :source, :external_id, :title, :content,
                     :lat, :lng, :timestamp, :status, :metadata_json,
                     :is_priority, :confidence_score)
            """, sig)
        except sqlite3.OperationalError:
            # Fallback: insert without confidence_score
            sig_copy = {k: v for k, v in sig.items() if k != "confidence_score"}
            cur = conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, metadata_json, is_priority)
                VALUES
                    (:signal_id, :source, :external_id, :title, :content,
                     :lat, :lng, :timestamp, :status, :metadata_json, :is_priority)
            """, sig_copy)

        if cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    return inserted, skipped

# ── Entry point ───────────────────────────────────────────────────────────────

def run(feed: str = DEFAULT_FEED,
        days: int = DEFAULT_DAYS,
        min_frp: float = MIN_FRP,
        db_path: Path | None = None,
        dry_run: bool = False) -> int:

    resolved_db = _resolve_db(str(db_path) if db_path else None)

    _t0 = time.monotonic()
    log(f"Feed     : {feed} ({days}d window)")
    log(f"Min FRP  : {min_frp} MW")
    log(f"Database : {resolved_db}")
    if dry_run:
        log("DRY RUN — no writes")

    try:
        url  = _feed_url(feed, days)
        rows = fetch_csv(url)
    except (RuntimeError, ValueError) as exc:
        err(str(exc))
        return 1

    log(f"CSV rows fetched: {len(rows)}")
    if not rows:
        log("Empty feed — nothing to do.")
        return 0

    signals = [
        s for r in rows
        if (s := row_to_signal(r, min_frp=min_frp)) is not None
    ]
    priority_count = sum(1 for s in signals if s["is_priority"])
    log(f"Mapped {len(signals)}/{len(rows)} rows → signals "
        f"({priority_count} priority, filtered {len(rows)-len(signals)} below FRP/invalid)")

    if dry_run:
        for s in signals[:5]:
            log(f"  [DRY] {s['title']} | conf={s['confidence_score']} | "
                f"priority={s['is_priority']}")
        if len(signals) > 5:
            log(f"  [DRY] … and {len(signals)-5} more")
        log("Dry run complete.")
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
        log("⚠  PRIORITY FIRE SIGNALS — check Signal Monitor at /signals")
    log_run(resolved_db, "firms_collector", "success",
            records_in=len(signals), records_out=inserted,
            duration_s=time.monotonic() - _t0)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORAGE FIRMS Fire Collector — ingest NASA MODIS/VIIRS fire data"
    )
    parser.add_argument(
        "--feed", default=DEFAULT_FEED,
        choices=list(FEED_VARIANTS.keys()),
        help=f"FIRMS feed variant (default: {DEFAULT_FEED})"
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        choices=[1, 2, 7],
        help=f"Time window in days (default: {DEFAULT_DAYS})"
    )
    parser.add_argument(
        "--min-frp", type=float, default=MIN_FRP, dest="min_frp",
        help=f"Minimum Fire Radiative Power in MW (default: {MIN_FRP})"
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Override path to database.db"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse without writing to database"
    )
    args = parser.parse_args()
    sys.exit(run(
        feed=args.feed,
        days=args.days,
        min_frp=args.min_frp,
        db_path=args.db,
        dry_run=args.dry_run,
    ))
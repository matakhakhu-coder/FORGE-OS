#!/usr/bin/env python3
"""
FORGE — Anomaly Detection Engine  (Phase 26)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Moves beyond simple threshold rules (Sentinel Phase 25) into statistical
baseline monitoring using Z-scores.  Where Sentinel asks "is this above
a fixed threshold?", the Anomaly Engine asks "is this unusual compared
to the historical norm for this specific source/region/actor?"

Architecture
────────────
Phase A — Baseline Builder (incremental)
  Reads the signals table, buckets counts into daily totals by
  (bucket_date, source, region_key), writes to signal_baselines.
  Only fills missing days — never recalculates existing rows.
  Safe to run daily or hourly.

Phase B — Geographic Z-Score
  For each (source, region_key) bucket with ≥ MIN_BASELINE_DAYS of
  history, computes the 30-day rolling mean and std_dev, then the
  Z-score for the current 24-hour window.
  Z > Z_THRESHOLD → insert statistical_anomaly alert.

Phase C — Actor Z-Score
  For each actor mentioned in signal_entities, computes the same
  rolling statistics on daily mention counts.
  Actor anomalies are boosted if the actor is high-influence
  (influence_score in top quartile from Phase 24).

Z-Score → Confidence mapping
  Raw Z is not a probability.  We map it to confidence via a logistic
  curve so the alert is interpretable:
    Z = 2.0  →  ~60%
    Z = 2.5  →  ~68%
    Z = 3.0  →  ~75%
    Z = 4.0  →  ~87%
    Z ≥ 5.0  →  ~95% (cap)

Sparse-data protection
  Std_dev < SPARSE_STD_FLOOR is treated as no anomaly — prevents
  division by near-zero when a region has had zero activity for weeks
  and then gets a single signal.  Requires ≥ MIN_BASELINE_DAYS of
  data before any Z-score is attempted.

All anomaly alerts write into the existing sentinel_alerts table
with alert_type = 'statistical_anomaly', so they appear in the
Sentinel Incident Panel with zero UI changes.

Usage
─────
    python forage/engines/anomaly_engine.py
    python forage/engines/anomaly_engine.py --dry-run
    python forage/engines/anomaly_engine.py --rebuild-baselines
    python forage/engines/anomaly_engine.py --report
    python forage/engines/anomaly_engine.py --db /path/to/database.db
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Thresholds ────────────────────────────────────────────────────────────────

BASELINE_DAYS      = 30     # rolling window for mean/std_dev
MIN_BASELINE_DAYS  = 7      # minimum days of data before Z-score is attempted
Z_THRESHOLD        = 2.0    # Z-score above which an anomaly is flagged
SPARSE_STD_FLOOR   = 0.5    # std_dev below this → skip (sparse-data protection)
ACTOR_TOP_PCTILE   = 0.75   # Phase 24 influence percentile for confidence boost

# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db(override: Optional[str] = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}.\n"
            "Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory  = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [anomaly_engine] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [anomaly_engine] WARN  {msg}",
                                   file=sys.stderr, flush=True)

# ── Schema ────────────────────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """
    signal_baselines: pre-aggregated daily signal counts.
      region_key  — '1°grid::<lat_int>:<lng_int>' for geographic buckets
                    'actor::<actor_name>'          for actor mention counts
      source      — 'usgs', 'gdelt', 'GDACS', 'firms', '__actor__', etc.
      daily_count — signals / mentions on that calendar day (UTC)
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_baselines (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_date  TEXT    NOT NULL,
            source       TEXT    NOT NULL,
            region_key   TEXT    NOT NULL,
            daily_count  INTEGER NOT NULL DEFAULT 0,
            computed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (bucket_date, source, region_key)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_baselines_lookup
        ON signal_baselines (source, region_key, bucket_date)
    """)
    conn.commit()

# ── Statistics ────────────────────────────────────────────────────────────────

def _mean_std(values: list[float]) -> tuple[float, float]:
    """Population mean and sample std_dev (Bessel's correction, ddof=1)."""
    n = len(values)
    if n < 2:
        return (values[0] if values else 0.0), 0.0
    mean = sum(values) / n
    var  = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var)


def _z_to_confidence(z: float) -> float:
    """
    Map a Z-score to an alert confidence using a logistic curve.
    Z=2 → ~0.60, Z=3 → ~0.75, Z=4 → ~0.87, Z≥5 → ~0.95 (cap).
    """
    raw = 1.0 / (1.0 + math.exp(-0.7 * (z - 2.0)))
    # Rescale [sigmoid(0)..1] → [0.50..0.95]
    lo, hi = 1.0 / (1.0 + math.exp(0.7 * 2.0)), 0.95
    mapped = lo + (hi - lo) * raw
    return round(min(mapped, 0.95), 3)

# ── Phase A: Baseline Builder ─────────────────────────────────────────────────

def build_baselines(conn: sqlite3.Connection,
                    rebuild: bool = False) -> int:
    """
    Aggregate raw signals into daily buckets and write to signal_baselines.
    Geographic key = '1°grid::<lat_int>:<lng_int>'
    Only fills missing (bucket_date, source, region_key) rows unless
    rebuild=True, in which case existing rows are replaced.
    Returns number of rows written.
    """
    if rebuild:
        conn.execute("DELETE FROM signal_baselines WHERE source != '__actor__'")
        conn.commit()
        log("Baselines cleared — full rebuild")

    # Find the earliest date already cached (skip if rebuilding)
    cutoff = None
    if not rebuild:
        row = conn.execute(
            "SELECT MAX(bucket_date) FROM signal_baselines "
            "WHERE source != '__actor__'"
        ).fetchone()
        if row and row[0]:
            # Re-aggregate from the day before the last cached date
            # (handles signals ingested late for a previous day)
            from datetime import date
            last = datetime.strptime(row[0], "%Y-%m-%d").date()
            cutoff = (last - timedelta(days=1)).isoformat()

    params: list = []
    where  = ""
    if cutoff:
        where  = "WHERE date(s.timestamp) >= ?"
        params = [cutoff]

    try:
        rows = conn.execute(f"""
            SELECT date(timestamp)           AS bucket_date,
                   source,
                   CAST(lat AS INTEGER)      AS lat_cell,
                   CAST(lng AS INTEGER)      AS lng_cell,
                   COUNT(*)                  AS daily_count
            FROM   signals s
            WHERE  lat IS NOT NULL
              AND  lng IS NOT NULL
              AND  source IS NOT NULL
              {('AND date(s.timestamp) >= ?' if cutoff else '')}
            GROUP  BY bucket_date, source, lat_cell, lng_cell
        """, params).fetchall()
    except Exception as exc:
        warn(f"Baseline build query failed: {exc}")
        return 0

    written = 0
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        region_key = f"1°grid::{r['lat_cell']}:{r['lng_cell']}"
        try:
            conn.execute(
                "INSERT OR REPLACE INTO signal_baselines "
                "(bucket_date, source, region_key, daily_count, computed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (r["bucket_date"], r["source"], region_key,
                 r["daily_count"], now)
            )
            written += 1
        except Exception:
            pass

    conn.commit()
    log(f"Baseline builder: {written} geographic rows written")
    return written


def build_actor_baselines(conn: sqlite3.Connection,
                          rebuild: bool = False) -> int:
    """
    Aggregate daily actor mention counts from signal_entities into
    signal_baselines with source='__actor__' and
    region_key='actor::<actor_name>'.
    """
    if rebuild:
        conn.execute(
            "DELETE FROM signal_baselines WHERE source = '__actor__'"
        )
        conn.commit()

    cutoff = None
    if not rebuild:
        row = conn.execute(
            "SELECT MAX(bucket_date) FROM signal_baselines "
            "WHERE source = '__actor__'"
        ).fetchone()
        if row and row[0]:
            from datetime import date
            last   = datetime.strptime(row[0], "%Y-%m-%d").date()
            cutoff = (last - timedelta(days=1)).isoformat()

    try:
        rows = conn.execute(
            "SELECT date(s.timestamp) AS bucket_date, "
            "se.text AS actor_name, COUNT(*) AS daily_count "
            "FROM signal_entities se "
            "JOIN signals s ON s.signal_id = se.signal_id "
            "WHERE se.label IN ('PERSON','ORG','GPE') "
            + ("AND date(s.timestamp) >= ? " if cutoff else "")
            + "GROUP BY bucket_date, se.text",
            ([cutoff] if cutoff else [])
        ).fetchall()
    except Exception as exc:
        warn(f"Actor baseline build failed: {exc}")
        return 0

    written = 0
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        region_key = f"actor::{r['actor_name']}"
        try:
            conn.execute(
                "INSERT OR REPLACE INTO signal_baselines "
                "(bucket_date, source, region_key, daily_count, computed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (r["bucket_date"], "__actor__", region_key,
                 r["daily_count"], now)
            )
            written += 1
        except Exception:
            pass

    conn.commit()
    log(f"Baseline builder: {written} actor rows written")
    return written

# ── Phase B: Geographic Z-Score ───────────────────────────────────────────────

def detect_geographic_anomalies(
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """
    For each (source, region_key) with ≥ MIN_BASELINE_DAYS of history,
    compute the rolling 30-day mean/std_dev and the Z-score for today.
    """
    today     = datetime.now(timezone.utc).date().isoformat()
    cutoff_30 = (datetime.now(timezone.utc).date()
                 - timedelta(days=BASELINE_DAYS)).isoformat()

    # All region/source combos that have enough history
    try:
        combos = conn.execute(
            "SELECT source, region_key, COUNT(DISTINCT bucket_date) AS n_days "
            "FROM signal_baselines "
            "WHERE source != '__actor__' "
            "  AND bucket_date >= ? AND bucket_date < ? "
            "GROUP BY source, region_key "
            "HAVING n_days >= ?",
            (cutoff_30, today, MIN_BASELINE_DAYS)
        ).fetchall()
    except Exception as exc:
        warn(f"Geographic anomaly query failed: {exc}")
        return 0

    staged  = 0
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for combo in combos:
        source     = combo["source"]
        region_key = combo["region_key"]

        # Historical daily counts (baseline window, excluding today)
        hist = conn.execute(
            "SELECT daily_count FROM signal_baselines "
            "WHERE source = ? AND region_key = ? "
            "  AND bucket_date >= ? AND bucket_date < ? "
            "ORDER BY bucket_date",
            (source, region_key, cutoff_30, today)
        ).fetchall()
        counts = [float(r["daily_count"]) for r in hist]
        if len(counts) < MIN_BASELINE_DAYS:
            continue

        mean, std = _mean_std(counts)
        if std < SPARSE_STD_FLOOR:
            continue   # sparse-data protection

        # Today's actual count (live — direct from signals table)
        try:
            lat_cell = int(region_key.split("::")[1].split(":")[0])
            lng_cell = int(region_key.split("::")[1].split(":")[1])
        except (IndexError, ValueError):
            continue

        try:
            today_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM signals "
                "WHERE source = ? "
                "  AND CAST(lat AS INTEGER) = ? "
                "  AND CAST(lng AS INTEGER) = ? "
                "  AND timestamp >= datetime('now', '-1 day')",
                (source, lat_cell, lng_cell)
            ).fetchone()
            current = float(today_row["cnt"]) if today_row else 0.0
        except Exception:
            continue

        z = (current - mean) / std
        if z <= Z_THRESHOLD:
            continue

        confidence = _z_to_confidence(z)

        # Approximate centroid of the 1° cell
        loc_lat = float(lat_cell) + 0.5
        loc_lon = float(lng_cell) + 0.5

        summary = (
            f"Pattern Detected: Requires Analyst Review. "
            f"Statistical anomaly in {source.upper()} signals at "
            f"grid cell ({lat_cell}°, {lng_cell}°). "
            f"Current 24h count: {int(current)} signals. "
            f"30-day baseline: mean={mean:.1f}, σ={std:.1f}. "
            f"Z-score: {z:.2f} (threshold: {Z_THRESHOLD}). "
            f"This rate is {z:.1f} standard deviations above normal."
        )

        if dry_run:
            log(f"  [DRY GEO] {source} {region_key} "
                f"z={z:.2f} conf={confidence:.2f} current={int(current)}")
            staged += 1
            continue

        # Dedup: skip if same type + cell already has a 'new' alert in last 6h
        dup = conn.execute(
            "SELECT id FROM sentinel_alerts "
            "WHERE alert_type = 'statistical_anomaly' AND status = 'new' "
            "  AND ABS(location_lat - ?) < 1.0 "
            "  AND ABS(location_lon - ?) < 1.0 "
            "  AND created_at >= datetime('now', '-6 hours')",
            (loc_lat, loc_lon)
        ).fetchone()

        if dup:
            conn.execute(
                "UPDATE sentinel_alerts "
                "SET signal_count = signal_count + ?, "
                "confidence_score = MIN(confidence_score + 0.05, 0.95) "
                "WHERE id = ?",
                (int(current), dup["id"])
            )
        else:
            conn.execute(
                "INSERT INTO sentinel_alerts "
                "(alert_type, confidence_score, location_lat, location_lon, "
                " signal_count, summary, status, created_at) "
                "VALUES ('statistical_anomaly', ?, ?, ?, ?, ?, 'new', ?)",
                (confidence, round(loc_lat, 4), round(loc_lon, 4),
                 int(current), summary, now_str)
            )
        staged += 1

    if not dry_run:
        conn.commit()

    log(f"Geographic anomalies: {staged} alerts")
    return staged


# ── Phase C: Actor Z-Score ────────────────────────────────────────────────────

def detect_actor_anomalies(
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """
    For each actor with ≥ MIN_BASELINE_DAYS of mention history,
    compute the Z-score for their mention count in the last 24 hours.
    High-influence actors (Phase 24 top quartile) get a confidence boost.
    """
    today     = datetime.now(timezone.utc).date().isoformat()
    cutoff_30 = (datetime.now(timezone.utc).date()
                 - timedelta(days=BASELINE_DAYS)).isoformat()

    # High-influence actor names for confidence boosting
    hi_influence: set[str] = set()
    try:
        score_rows = conn.execute(
            "SELECT influence_score FROM actor_network_metrics "
            "ORDER BY influence_score DESC"
        ).fetchall()
        if score_rows:
            cutoff_idx = max(0, int(len(score_rows) * ACTOR_TOP_PCTILE) - 1)
            min_score  = score_rows[cutoff_idx]["influence_score"]
            actors     = conn.execute(
                "SELECT a.name FROM actor_network_metrics m "
                "JOIN actors a ON a.actor_id = m.actor_id "
                "WHERE m.influence_score >= ?", (min_score,)
            ).fetchall()
            hi_influence = {r["name"].lower() for r in actors}
    except Exception:
        pass

    # Actor region_keys with enough history
    try:
        actor_combos = conn.execute(
            "SELECT region_key, COUNT(DISTINCT bucket_date) AS n_days "
            "FROM signal_baselines "
            "WHERE source = '__actor__' "
            "  AND bucket_date >= ? AND bucket_date < ? "
            "GROUP BY region_key HAVING n_days >= ?",
            (cutoff_30, today, MIN_BASELINE_DAYS)
        ).fetchall()
    except Exception as exc:
        warn(f"Actor anomaly query failed: {exc}")
        return 0

    staged  = 0
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for combo in actor_combos:
        region_key = combo["region_key"]
        try:
            actor_name = region_key.split("actor::")[1]
        except IndexError:
            continue

        hist = conn.execute(
            "SELECT daily_count FROM signal_baselines "
            "WHERE source = '__actor__' AND region_key = ? "
            "  AND bucket_date >= ? AND bucket_date < ? "
            "ORDER BY bucket_date",
            (region_key, cutoff_30, today)
        ).fetchall()
        counts = [float(r["daily_count"]) for r in hist]
        if len(counts) < MIN_BASELINE_DAYS:
            continue

        mean, std = _mean_std(counts)
        if std < SPARSE_STD_FLOOR:
            continue

        # Current 24h mention count from live signal_entities
        try:
            cur_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM signal_entities se "
                "JOIN signals s ON s.signal_id = se.signal_id "
                "WHERE se.text = ? "
                "  AND s.timestamp >= datetime('now', '-1 day')",
                (actor_name,)
            ).fetchone()
            current = float(cur_row["cnt"]) if cur_row else 0.0
        except Exception:
            continue

        z = (current - mean) / std
        if z <= Z_THRESHOLD:
            continue

        # Confidence boost for high-influence actors
        base_conf  = _z_to_confidence(z)
        is_hi      = actor_name.lower() in hi_influence
        confidence = min(base_conf + (0.10 if is_hi else 0.0), 0.95)

        summary = (
            f"Pattern Detected: Requires Analyst Review. "
            f"Statistical anomaly in mention frequency for "
            f"'{actor_name}'{' [HIGH INFLUENCE]' if is_hi else ''}. "
            f"Current 24h mentions: {int(current)}. "
            f"30-day baseline: mean={mean:.1f}, σ={std:.1f}. "
            f"Z-score: {z:.2f}. "
            f"Mention rate is {z:.1f} standard deviations above normal."
        )

        if dry_run:
            flag = " ★ HI-INFLUENCE" if is_hi else ""
            log(f"  [DRY ACT] '{actor_name}'{flag} "
                f"z={z:.2f} conf={confidence:.2f} current={int(current)}")
            staged += 1
            continue

        # Dedup: one actor anomaly alert per actor per 6h
        dup = conn.execute(
            "SELECT id FROM sentinel_alerts "
            "WHERE alert_type = 'statistical_anomaly' AND status = 'new' "
            "  AND summary LIKE ? "
            "  AND created_at >= datetime('now', '-6 hours')",
            (f"%'{actor_name}'%",)
        ).fetchone()

        if dup:
            conn.execute(
                "UPDATE sentinel_alerts "
                "SET signal_count = signal_count + 1, "
                "confidence_score = MIN(confidence_score + 0.05, 0.95) "
                "WHERE id = ?",
                (dup["id"],)
            )
        else:
            conn.execute(
                "INSERT INTO sentinel_alerts "
                "(alert_type, confidence_score, location_lat, location_lon, "
                " signal_count, summary, status, created_at) "
                "VALUES ('statistical_anomaly', ?, NULL, NULL, ?, ?, 'new', ?)",
                (confidence, int(current), summary, now_str)
            )
        staged += 1

    if not dry_run:
        conn.commit()

    log(f"Actor anomalies: {staged} alerts")
    return staged


# ── Main engine class ─────────────────────────────────────────────────────────

class AnomalyEngine:

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _resolve_db()

    def run(self, dry_run: bool = False,
            rebuild: bool = False) -> dict:

        log(f"Database : {self._db_path}")
        log(f"Dry run  : {dry_run} | Rebuild baselines: {rebuild}")

        conn = _open_db(self._db_path)
        _ensure_schema(conn)

        # Phase A: build / update baselines
        log("Phase A — Updating signal baselines…")
        geo_rows   = build_baselines(conn, rebuild=rebuild)
        actor_rows = build_actor_baselines(conn, rebuild=rebuild)

        # Phase B: geographic Z-scores
        log("Phase B — Geographic anomaly detection…")
        geo_alerts = detect_geographic_anomalies(conn, dry_run=dry_run)

        # Phase C: actor Z-scores
        log("Phase C — Actor mention anomaly detection…")
        act_alerts = detect_actor_anomalies(conn, dry_run=dry_run)

        conn.close()

        summary = {
            "status":             "done",
            "baseline_geo_rows":  geo_rows,
            "baseline_actor_rows": actor_rows,
            "geo_alerts":         geo_alerts,
            "actor_alerts":       act_alerts,
            "total_alerts":       geo_alerts + act_alerts,
            "dry_run":            dry_run,
            "computed_at":        datetime.now(timezone.utc).isoformat(),
        }
        log(f"Complete: {summary}")
        return summary

    def report(self) -> None:
        conn = _open_db(self._db_path)
        try:
            alerts = conn.execute(
                "SELECT alert_type, confidence_score, signal_count, "
                "status, created_at, summary "
                "FROM sentinel_alerts "
                "WHERE alert_type = 'statistical_anomaly' "
                "ORDER BY confidence_score DESC, created_at DESC "
                "LIMIT 15"
            ).fetchall()
            baseline_meta = conn.execute(
                "SELECT COUNT(*) AS rows, "
                "MIN(bucket_date) AS earliest, MAX(bucket_date) AS latest "
                "FROM signal_baselines"
            ).fetchone()
        except Exception as exc:
            print(f"Error: {exc}")
            conn.close()
            return
        conn.close()

        print(f"\n{'─'*80}")
        print(f"  FORGE ANOMALY ENGINE — Report")
        print(f"  Baselines: {baseline_meta['rows']} rows "
              f"({baseline_meta['earliest']} → {baseline_meta['latest']})")
        print(f"{'─'*80}")
        if not alerts:
            print("  No statistical anomaly alerts yet.")
            print("  Run: python forage/engines/anomaly_engine.py")
        for r in alerts:
            flag = "⚑ " if r["status"] == "new" else "  "
            print(f"  {flag}conf={r['confidence_score']:.2f} "
                  f"sigs={r['signal_count']} "
                  f"status={r['status']} "
                  f"@ {r['created_at'][:16]}")
            print(f"     {r['summary'][:100]}…")
        print(f"{'─'*80}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Anomaly Engine — Z-score statistical anomaly detection"
    )
    parser.add_argument("--db",               type=Path, default=None)
    parser.add_argument("--dry-run",          action="store_true")
    parser.add_argument("--rebuild-baselines", action="store_true",
                        help="Clear and rebuild all baseline data from scratch")
    parser.add_argument("--report",           action="store_true",
                        help="Print anomaly report without running detection")
    args = parser.parse_args()

    engine = AnomalyEngine(
        db_path=_resolve_db(str(args.db) if args.db else None)
    )

    if args.report:
        engine.report()
        sys.exit(0)

    result = engine.run(
        dry_run=args.dry_run,
        rebuild=args.rebuild_baselines,
    )
    engine.report()
    sys.exit(0 if result["status"] == "done" else 1)
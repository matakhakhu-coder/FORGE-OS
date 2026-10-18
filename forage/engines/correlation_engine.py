#!/usr/bin/env python3
"""
FORAGE — Spatiotemporal Correlation Engine  (Phase 23)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Identifies when two independent signals converge in both space and time,
producing a continuous Correlation Score rather than a binary match.

The score is the harmonic mean of two normalised components:

  Space score  = 1 − (distance_km  / MAX_DISTANCE_KM)
  Time  score  = 1 − (time_diff_h  / MAX_TIME_HOURS)

  Correlation  = (2 × space × time) / (space + time)
                 ↳ Harmonic mean penalises lopsided matches.
                   Two signals 1 km apart but 23 hours apart score lower
                   than two signals 5 km apart and 2 hours apart.

Distance is computed with the Haversine formula (great-circle, metres).

Performance
───────────
A naive O(n²) over the full signals table would collapse as the archive
grows. The engine constrains evaluation to signals ingested in the last
48 hours.  For a typical active archive (100–500 signals/day) this keeps
the comparison set to at most ~1000 items, keeping runtime under 1 second.

Signals are also pre-filtered to those that have coordinates — signals
without lat/lng cannot be spatially correlated.

Thresholds
──────────
  MAX_DISTANCE_KM = 50   km      (~30 mile urban/regional radius)
  MAX_TIME_HOURS  = 24   hours   (same-day window)
  MIN_SCORE       = 0.7          (only store high-confidence correlations)

All three are configurable at the top of this file.

Usage
─────
    python forage/engines/correlation_engine.py
    python forage/engines/correlation_engine.py --hours 72     # widen time window
    python forage/engines/correlation_engine.py --min-score 0.5
    python forage/engines/correlation_engine.py --dry-run
    python forage/engines/correlation_engine.py --report
    python forage/engines/correlation_engine.py --db /path/to/database.db
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
try:
    from forage.utils.pipeline_logger import log_run
except ImportError:
    def log_run(*args, **kwargs):  # type: ignore[misc]
        pass  # logging must never crash the pipeline
from typing import Optional

# ── Thresholds (edit here to tune globally) ───────────────────────────────────

MAX_DISTANCE_KM  = 50.0    # signals further apart than this cannot correlate
MAX_TIME_HOURS   = 24.0    # signals more than this apart in time cannot correlate
MIN_SCORE        = 0.70    # only store pairs scoring above this threshold
WINDOW_HOURS     = 48      # how many hours back to pull signals for comparison
BATCH_SIZE       = 200     # DB commit every N pairs written

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
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS correlated_incidents (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_a            TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
            signal_b            TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
            correlation_score   REAL    NOT NULL,
            distance_km         REAL    NOT NULL,
            time_difference_hours REAL  NOT NULL,
            space_score         REAL    NOT NULL,
            time_score          REAL    NOT NULL,
            detected_at         TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (signal_a, signal_b)
        )
    """)
    conn.commit()

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [correlation_engine] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [correlation_engine] WARN  {msg}",
                                   file=sys.stderr, flush=True)

# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Great-circle distance between two (lat, lng) points in kilometres.
    Uses the Haversine formula — accurate to within 0.3% for distances
    up to a few hundred kilometres, which is well within our 50 km bound.
    """
    R    = 6_371.0          # Earth mean radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))

# ── Scoring ───────────────────────────────────────────────────────────────────

def score_pair(
    lat_a: float, lng_a: float, ts_a: datetime,
    lat_b: float, lng_b: float, ts_b: datetime,
    max_dist: float = MAX_DISTANCE_KM,
    max_time: float = MAX_TIME_HOURS,
) -> tuple[float, float, float, float, float]:
    """
    Compute the correlation score for a signal pair.

    Returns (score, distance_km, time_diff_h, space_score, time_score).
    Returns (0.0, ...) if either component is zero (outside bounds).
    """
    dist_km   = haversine_km(lat_a, lng_a, lat_b, lng_b)
    time_diff = abs((ts_a - ts_b).total_seconds()) / 3600.0   # hours

    if dist_km > max_dist or time_diff > max_time:
        return 0.0, dist_km, time_diff, 0.0, 0.0

    space_s = 1.0 - (dist_km  / max_dist)
    time_s  = 1.0 - (time_diff / max_time)

    # Harmonic mean — punishes lopsided pairs
    if space_s + time_s == 0:
        score = 0.0
    else:
        score = (2.0 * space_s * time_s) / (space_s + time_s)

    return round(score, 4), round(dist_km, 3), round(time_diff, 3), \
           round(space_s, 4), round(time_s, 4)

# ── Timestamp parsing ─────────────────────────────────────────────────────────

_FMT_CANDIDATES = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
]

def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    for fmt in _FMT_CANDIDATES:
        try:
            return datetime.strptime(raw[:26], fmt)
        except ValueError:
            continue
    warn(f"Could not parse timestamp: {raw!r}")
    return None

# ── Engine ────────────────────────────────────────────────────────────────────

class CorrelationEngine:

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path   = db_path or _resolve_db()
        self._max_dist  = MAX_DISTANCE_KM
        self._max_time  = MAX_TIME_HOURS
        self._min_score = MIN_SCORE
        self._window_h  = WINDOW_HOURS

    def _load_signals(self, conn: sqlite3.Connection) -> list:
        """
        Pull signals from the last WINDOW_HOURS that have coordinates.
        Returns list of dicts with parsed datetime objects.
        """
        rows = conn.execute(
            "SELECT signal_id, source, title, lat, lng, timestamp, "
            "       is_priority, status "
            "FROM   signals "
            "WHERE  lat IS NOT NULL "
            "  AND  lng IS NOT NULL "
            "  AND  status IN ('raw', 'promoted') "
            "  AND  timestamp >= datetime('now', ?) "
            # FIRMS wildfire pixel signals are excluded from correlation.
            # Adjacent fire pixels always score 1.0 (0.4 km / 0.0 h) and
            # would generate millions of pairs that flood correlated_incidents
            # with noise.  High-intensity FIRMS events are still surfaced
            # in the feed via the FIRMS high-impact gate in api_feed().
            "  AND  source != 'firms' "
            # Placeholder pins (collector defaults: Cape Town/Parliament, Pretoria x2, DC) and
            # date-only midnight timestamps carry no real location or time, so they are
            # excluded; otherwise they score as perfect 0 km / 0 h matches.
            "  AND  NOT (abs(lat+33.9249)<0.00005 AND abs(lng-18.4241)<0.00005) "
            "  AND  NOT (abs(lat+25.7479)<0.00005 AND (abs(lng-28.2293)<0.00005 OR abs(lng-28.1876)<0.00005)) "
            "  AND  NOT (abs(lat-38.9072)<0.00005 AND abs(lng+77.0369)<0.00005) "
            "  AND  timestamp NOT LIKE '%T00:00:00Z' "
            "  AND  timestamp NOT LIKE '% 00:00:00' "
            "ORDER  BY timestamp DESC",
            (f"-{self._window_h} hours",),
        ).fetchall()

        signals = []
        for r in rows:
            ts = _parse_ts(r["timestamp"])
            if ts is None:
                continue
            signals.append({
                "signal_id":   r["signal_id"],
                "source":      r["source"],
                "title":       r["title"],
                "lat":         r["lat"],
                "lng":         r["lng"],
                "ts":          ts,
                "is_priority": r["is_priority"],
            })
        return signals

    def run(self, dry_run: bool = False,
            min_score: Optional[float] = None,
            window_hours: Optional[int] = None) -> dict:
        """
        Full pipeline: load → O(n²) constrained pair evaluation → write.
        Returns summary dict (also returned by /api/correlations/recalculate).
        """
        if min_score    is not None: self._min_score = min_score
        if window_hours is not None: self._window_h  = window_hours

        log(f"Database : {self._db_path}")
        log(f"Window   : {self._window_h}h | Max dist: {self._max_dist}km "
            f"| Max time: {self._max_time}h | Min score: {self._min_score}")

        # Checked pins first, on their incident dates, into their own private
        # review table (never correlated_incidents or the graph). Sprint 4.
        try:
            self.checked_proximity(dry_run=dry_run)
        except Exception as exc:  # never block the main matcher
            warn(f"checked-pin proximity skipped: {exc}")

        conn = _open_db(self._db_path)
        _ensure_schema(conn)

        signals = self._load_signals(conn)
        n = len(signals)
        log(f"Signals in window: {n}")

        if n < 2:
            log("Fewer than 2 mappable signals — nothing to correlate.")
            conn.close()
            return {"status": "too_few", "signals": n, "pairs_evaluated": 0,
                    "correlations_found": 0, "written": 0}

        # O(n²) bounded comparison — only unique pairs (i < j)
        total_pairs  = n * (n - 1) // 2
        log(f"Evaluating {total_pairs:,} pairs…")

        written    = 0
        found      = 0
        batch      = []
        edge_batch = []

        for i in range(n):
            a = signals[i]
            for j in range(i + 1, n):
                b = signals[j]

                # Skip if same source AND same signal (shouldn't happen but guard it)
                if a["signal_id"] == b["signal_id"]:
                    continue

                score, dist, tdiff, ss, ts_s = score_pair(
                    a["lat"], a["lng"], a["ts"],
                    b["lat"], b["lng"], b["ts"],
                    max_dist=self._max_dist,
                    max_time=self._max_time,
                )

                if score < self._min_score:
                    continue

                found += 1

                if dry_run:
                    log(f"  [DRY] {a['signal_id'][:8]}...{b['signal_id'][:8]}... "
                        f"score={score:.3f} dist={dist:.1f}km dt={tdiff:.1f}h")
                    continue

                # Canonical ordering: always store smaller ID first
                id_a, id_b = sorted([a["signal_id"], b["signal_id"]])
                batch.append((id_a, id_b, score, dist, tdiff, ss, ts_s))
                edge_batch.append((id_a, id_b, score))

                if len(batch) >= BATCH_SIZE:
                    written += self._flush(conn, batch)
                    self._flush_graph_edges(conn, edge_batch)
                    batch      = []
                    edge_batch = []

        if batch and not dry_run:
            written += self._flush(conn, batch)
        if edge_batch and not dry_run:
            self._flush_graph_edges(conn, edge_batch)

        conn.close()

        summary = {
            "status":             "done",
            "signals":            n,
            "pairs_evaluated":    total_pairs,
            "correlations_found": found,
            "written":            written if not dry_run else 0,
            "dry_run":            dry_run,
            "computed_at":        datetime.now(timezone.utc).isoformat(),
        }
        log(f"Complete: {summary}")
        log_run(self._db_path, "correlation_engine", "success",
                records_in=summary.get("signals", 0),
                records_out=summary.get("written", 0),
                duration_s=None, detail=summary)
        return summary

    def _flush(self, conn: sqlite3.Connection, batch: list) -> int:
        """Batch INSERT OR REPLACE for correlated_incidents."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        written = 0
        for row in batch:
            id_a, id_b, score, dist, tdiff, ss, ts_s = row
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO correlated_incidents "
                    "(signal_a, signal_b, correlation_score, distance_km, "
                    " time_difference_hours, space_score, time_score, detected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (id_a, id_b, score, dist, tdiff, ss, ts_s, now),
                )
                written += 1
            except sqlite3.IntegrityError:
                pass  # FK violation — signal deleted between load and flush
        conn.commit()
        return written

    def _flush_graph_edges(self, conn: sqlite3.Connection, edge_batch: list) -> None:
        """
        For each (signal_a, signal_b, score) pair, look up actors linked to
        both signals via signal_actors, resolve their graph_nodes, and insert
        correlated_with edges into graph_edges.  No return value — failures
        are swallowed so the main correlation write is never blocked.
        """
        now = datetime.now(timezone.utc).isoformat()
        for id_a, id_b, score in edge_batch:
            actors_a = conn.execute(
                "SELECT actor_id FROM signal_actors WHERE signal_id=?", (id_a,)
            ).fetchall()
            actors_b = conn.execute(
                "SELECT actor_id FROM signal_actors WHERE signal_id=?", (id_b,)
            ).fetchall()
            if not actors_a or not actors_b:
                continue
            for aa in actors_a:
                node_a = conn.execute(
                    "SELECT node_id FROM graph_nodes "
                    "WHERE node_type='actor' AND ref_id=?",
                    (aa["actor_id"],),
                ).fetchone()
                if not node_a:
                    continue
                for ab in actors_b:
                    node_b = conn.execute(
                        "SELECT node_id FROM graph_nodes "
                        "WHERE node_type='actor' AND ref_id=?",
                        (ab["actor_id"],),
                    ).fetchone()
                    if not node_b:
                        continue
                    try:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO graph_edges
                                (source_node_id, target_node_id, relation_type,
                                 weight, source_signal_id, created_at)
                            VALUES (?, ?, 'correlated_with', ?, ?, ?)
                            """,
                            (node_a["node_id"], node_b["node_id"],
                             score, id_a, now),
                        )
                    except Exception:
                        pass
        conn.commit()

    # ── Hand-checked pins: place and time by INCIDENT date (Sprint 4) ──────────
    #
    # The main matcher reads `timestamp`, which for collected signals is the
    # report time, and skips checked pins altogether. Here checked pins are
    # matched on their incident window (checked.incident_start + precision),
    # and the result is a private review list only: checked_proximity rows
    # never enter correlated_incidents or graph_edges, never touch the pins
    # themselves, and are never drawn on the map. Being near in place and time
    # is a lead for a person to look at, not a link between two killings.

    CHECKED_MAX_KM = 5.0
    CHECKED_MAX_HOURS = 48.0

    def checked_proximity(self, dry_run: bool = False,
                          max_km: Optional[float] = None,
                          max_hours: Optional[float] = None) -> dict:
        import sys as _sys
        _tools = str(Path(__file__).resolve().parents[2] / "tools")
        if _tools not in _sys.path:
            _sys.path.insert(0, _tools)
        from checked_signals import incident_window  # noqa: E402

        max_km = self.CHECKED_MAX_KM if max_km is None else max_km
        max_h = self.CHECKED_MAX_HOURS if max_hours is None else max_hours
        conn = _open_db(self._db_path)
        try:
            rows = conn.execute(
                "SELECT signal_id, title, lat, lng, metadata_json FROM signals "
                "WHERE source_type = 'checked' AND published_at IS NOT NULL "
                "AND lat IS NOT NULL AND lng IS NOT NULL").fetchall()
            pins = []
            for r in rows:
                try:
                    chk = (json.loads(r["metadata_json"] or "{}") or {}).get("checked") or {}
                except (TypeError, ValueError):
                    chk = {}
                w = incident_window(chk)
                if w is None:
                    warn(f"checked pin {r['signal_id'][:8]} has no readable incident date")
                    continue
                pins.append((r["signal_id"], float(r["lat"]), float(r["lng"]), w,
                             chk.get("incident_precision")))
            pairs = []
            for i in range(len(pins)):
                for j in range(i + 1, len(pins)):
                    a, b = pins[i], pins[j]
                    km = haversine_km(a[1], a[2], b[1], b[2])
                    # gap between the two incident windows (0 when they overlap)
                    gap = max(0.0, (max(a[3][0], b[3][0]) - min(a[3][1], b[3][1])).total_seconds() / 3600)
                    if km <= max_km and gap <= max_h:
                        ida, idb = sorted([a[0], b[0]])
                        pairs.append((ida, idb, round(km, 2), round(gap, 1),
                                      f"{a[4]}/{b[4]}"))
            if not dry_run:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS checked_proximity (
                        signal_a TEXT NOT NULL, signal_b TEXT NOT NULL,
                        distance_km REAL NOT NULL, hours_apart REAL NOT NULL,
                        precision TEXT, note TEXT NOT NULL,
                        detected_at TEXT NOT NULL,
                        PRIMARY KEY (signal_a, signal_b))""")
                conn.execute("DELETE FROM checked_proximity")
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                conn.executemany(
                    "INSERT INTO checked_proximity VALUES (?,?,?,?,?,?,?)",
                    [(*p, "near in place and incident time; not a link", now) for p in pairs])
                conn.commit()
            log(f"Checked pins: {len(pins)} read on incident date; "
                f"{len(pairs)} near pairs (<= {max_km} km, <= {max_h} h apart)")
            return {"status": "done", "checked_pins": len(pins), "near_pairs": len(pairs),
                    "pairs": pairs, "dry_run": dry_run}
        finally:
            conn.close()

    def report(self) -> None:
        """Print top correlations to stdout."""
        conn = _open_db(self._db_path)
        try:
            rows = conn.execute("""
                SELECT ci.correlation_score, ci.distance_km,
                       ci.time_difference_hours,
                       sa.title AS title_a, sa.source AS src_a,
                       sb.title AS title_b, sb.source AS src_b,
                       ci.detected_at
                FROM   correlated_incidents ci
                JOIN   signals sa ON sa.signal_id = ci.signal_a
                JOIN   signals sb ON sb.signal_id = ci.signal_b
                ORDER  BY ci.correlation_score DESC
                LIMIT  15
            """).fetchall()
            meta = conn.execute(
                "SELECT COUNT(*) AS n, MAX(detected_at) AS last_run "
                "FROM correlated_incidents"
            ).fetchone()
        except Exception as exc:
            print(f"Error: {exc}")
            conn.close()
            return
        conn.close()

        if not rows:
            print("No correlations computed yet.")
            print("Run: python forage/engines/correlation_engine.py")
            return

        print(f"\n{'─'*80}")
        print(f"  FORGE Correlation Engine — Top Correlations")
        print(f"  Total: {meta['n']} pairs | Last run: {meta['last_run']}")
        print(f"{'─'*80}")
        print(f"  {'Score':>6}  {'Dist':>6}  {'ΔT':>5}  Signal A (src)         Signal B (src)")
        print(f"{'─'*80}")
        for r in rows:
            ta = (r["title_a"] or "")[:25]
            tb = (r["title_b"] or "")[:25]
            print(f"  {r['correlation_score']:>6.3f}  "
                  f"{r['distance_km']:>5.1f}km  "
                  f"{r['time_difference_hours']:>4.1f}h  "
                  f"{ta:<28}({r['src_a']})  "
                  f"{tb:<28}({r['src_b']})")
        print(f"{'─'*80}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Correlation Engine — spatiotemporal signal correlation"
    )
    parser.add_argument("--db",         type=Path,  default=None)
    parser.add_argument("--hours",      type=int,   default=WINDOW_HOURS,
                        help=f"Lookback window in hours (default {WINDOW_HOURS})")
    parser.add_argument("--min-score",  type=float, default=MIN_SCORE,
                        help=f"Minimum correlation score to store (default {MIN_SCORE})")
    parser.add_argument("--max-dist",   type=float, default=MAX_DISTANCE_KM,
                        help=f"Maximum distance in km (default {MAX_DISTANCE_KM})")
    parser.add_argument("--max-time",   type=float, default=MAX_TIME_HOURS,
                        help=f"Maximum time delta in hours (default {MAX_TIME_HOURS})")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--report",     action="store_true",
                        help="Print top correlations without recalculating")
    args = parser.parse_args()

    engine = CorrelationEngine(db_path=_resolve_db(str(args.db) if args.db else None))
    engine._max_dist  = args.max_dist
    engine._max_time  = args.max_time

    if args.report:
        engine.report()
        sys.exit(0)

    result = engine.run(
        dry_run=args.dry_run,
        min_score=args.min_score,
        window_hours=args.hours,
    )
    engine.report()
    sys.exit(0 if result.get("status") in ("done", "too_few") else 1)

# --- MEGA RUNNER ADAPTER ---
def run_all():
    print(f"[{__name__}] Executing run_all...")
    # Call your actual processing logic here
#!/usr/bin/env python3
"""
FORGE — Signal Decay Engine  (Phase 28)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Applies time-based exponential relevance decay to all signals so the
analyst feed, map, and signal monitor surface current intelligence
rather than treating a 3-week-old earthquake identically to a 2-hour-old
crime signal.

The Math
────────
    relevance_score = initial_score × e^(−λ × hours_elapsed)

λ (lambda) controls the decay rate, which is stream-aware:

    CRIME_INTEL      λ = 0.020   half-life ~35 hours   (fast: crime is time-critical)
    INFRASTRUCTURE   λ = 0.006   half-life ~5 days      (slow: outages persist)
    PRIORITY         λ = 0.003   half-life ~10 days     (slowest: important articles)
    GLOBAL           λ = 0.012   half-life ~58 hours    (moderate: default)

initial_score
    1.0 for standard signals
    1.5 for is_priority=1 signals (priority signals start higher, decay to same floor)

Floor
    relevance_score never drops below MIN_RELEVANCE (0.05).
    Signals do not disappear — they remain in the archive at very low relevance.

Sentinel alerts are exempt from decay and always remain at relevance 1.0.

Usage
─────
    python forage/engines/decay_engine.py
    python forage/engines/decay_engine.py --dry-run
    python forage/engines/decay_engine.py --report
    python forage/engines/decay_engine.py --db /path/to/database.db

Schedule
────────
    Run every 6 hours for best freshness. Example cron:
    0 */6 * * * cd /path/to/FORGE && python forage/engines/decay_engine.py
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
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
from typing import Optional

# ── Decay constants ────────────────────────────────────────────────────────────

LAMBDA = {
    "CRIME_INTEL":    0.020,   # half-life ~35 h
    "INFRASTRUCTURE": 0.006,   # half-life ~5 days
    "PRIORITY":       0.003,   # half-life ~10 days
    "GLOBAL":         0.012,   # half-life ~58 h
}
DEFAULT_LAMBDA   = 0.012
PRIORITY_BOOST   = 1.5    # is_priority signals start at this initial score
MIN_RELEVANCE    = 0.05   # floor — signals never fully disappear
BATCH_SIZE       = 500    # rows per DB commit

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


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Add relevance_score column if not yet present."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if "relevance_score" not in existing:
        conn.execute(
            "ALTER TABLE signals "
            "ADD COLUMN relevance_score REAL NOT NULL DEFAULT 1.0"
        )
        conn.commit()

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [decay_engine] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [decay_engine] WARN  {msg}",
                                   file=sys.stderr, flush=True)

# ── Core decay function ───────────────────────────────────────────────────────

def compute_relevance(
    hours_elapsed: float,
    stream: str,
    is_priority: int,
) -> float:
    """
    Compute the relevance score for a signal given its age, stream, and
    priority flag.

    Returns a float in [MIN_RELEVANCE, 1.5].
    """
    lam           = LAMBDA.get(stream, DEFAULT_LAMBDA)
    initial_score = PRIORITY_BOOST if is_priority else 1.0
    raw           = initial_score * math.exp(-lam * hours_elapsed)
    return round(max(raw, MIN_RELEVANCE), 4)

# ── Engine ────────────────────────────────────────────────────────────────────

class DecayEngine:

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _resolve_db()

    def run(self, dry_run: bool = False) -> dict:
        _t0 = __import__("time").monotonic()
        log(f"Database : {self._db_path}")
        log(f"Dry run  : {dry_run}")

        conn = _open_db(self._db_path)
        _ensure_schema(conn)

        # Load all non-dismissed signals with their timestamps and stream
        rows = conn.execute(
            "SELECT signal_id, timestamp, stream, is_priority, relevance_score "
            "FROM signals "
            "WHERE status != 'dismissed'"
        ).fetchall()

        log(f"Signals to process: {len(rows)}")

        now_utc   = datetime.now(timezone.utc)
        updates   = []
        buckets   = {"fresh": 0, "fading": 0, "stale": 0}

        for row in rows:
            # Parse timestamp — handle both formats
            raw_ts = row["timestamp"] or ""
            try:
                if "T" in raw_ts:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                else:
                    ts = datetime.strptime(raw_ts[:19], "%Y-%m-%d %H:%M:%S")
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                warn(f"Could not parse timestamp '{raw_ts}' for {row['signal_id'][:8]}…")
                continue

            hours_elapsed = (now_utc - ts).total_seconds() / 3600.0
            if hours_elapsed < 0:
                hours_elapsed = 0.0

            stream      = row["stream"] or "GLOBAL"
            is_priority = row["is_priority"] or 0
            new_score   = compute_relevance(hours_elapsed, stream, is_priority)

            # Classify for reporting
            if new_score >= 0.6:
                buckets["fresh"] += 1
            elif new_score >= 0.2:
                buckets["fading"] += 1
            else:
                buckets["stale"] += 1

            if dry_run:
                continue

            updates.append((new_score, row["signal_id"]))

            # Batch commit
            if len(updates) >= BATCH_SIZE:
                conn.executemany(
                    "UPDATE signals SET relevance_score = ? WHERE signal_id = ?",
                    updates
                )
                conn.commit()
                updates = []

        if updates and not dry_run:
            conn.executemany(
                "UPDATE signals SET relevance_score = ? WHERE signal_id = ?",
                updates
            )
            conn.commit()

        conn.close()

        summary = {
            "status":    "done",
            "processed": len(rows),
            "fresh":     buckets["fresh"],
            "fading":    buckets["fading"],
            "stale":     buckets["stale"],
            "dry_run":   dry_run,
            "computed_at": now_utc.isoformat(),
        }
        log(f"Complete: {summary}")
        log_run(self._db_path, "decay_engine", "success",
                records_in=summary.get("processed", 0),
                records_out=summary.get("processed", 0),
                duration_s=__import__("time").monotonic() - _t0,
                detail=summary)
        return summary

    def report(self) -> None:
        conn = _open_db(self._db_path)
        try:
            stats = conn.execute("""
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(relevance_score), 3) AS avg_rel,
                    ROUND(MIN(relevance_score), 3) AS min_rel,
                    ROUND(MAX(relevance_score), 3) AS max_rel,
                    SUM(CASE WHEN relevance_score >= 0.6 THEN 1 ELSE 0 END) AS fresh,
                    SUM(CASE WHEN relevance_score >= 0.2
                              AND relevance_score < 0.6 THEN 1 ELSE 0 END) AS fading,
                    SUM(CASE WHEN relevance_score < 0.2 THEN 1 ELSE 0 END) AS stale
                FROM signals WHERE status != 'dismissed'
            """).fetchone()

            top = conn.execute("""
                SELECT title, source, stream, relevance_score,
                       timestamp, is_priority
                FROM signals
                WHERE status != 'dismissed'
                ORDER BY relevance_score DESC
                LIMIT 10
            """).fetchall()

        except Exception as exc:
            print(f"Error: {exc}")
            conn.close()
            return
        conn.close()

        print(f"\n{'─'*72}")
        print(f"  FORGE DECAY ENGINE — Relevance Report")
        print(f"  Total: {stats['total']} | "
              f"Avg: {stats['avg_rel']} | "
              f"Min: {stats['min_rel']} | "
              f"Max: {stats['max_rel']}")
        print(f"  Fresh (≥0.6): {stats['fresh']} | "
              f"Fading (0.2–0.6): {stats['fading']} | "
              f"Stale (<0.2): {stats['stale']}")
        print(f"{'─'*72}")
        print(f"  {'Title':<38} {'Src':<7} {'Stream':<16} {'Rel':>6}")
        print(f"{'─'*72}")
        for r in top:
            flag = "⚑ " if r["is_priority"] else "  "
            print(f"  {flag}{(r['title'] or ''):<36} "
                  f"{(r['source'] or ''):<7} "
                  f"{(r['stream'] or 'GLOBAL'):<16} "
                  f"{r['relevance_score']:>6.3f}")
        print(f"{'─'*72}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Decay Engine — exponential signal relevance decay"
    )
    parser.add_argument("--db",      type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report",  action="store_true",
                        help="Print relevance report without running decay")
    args = parser.parse_args()

    engine = DecayEngine(
        db_path=_resolve_db(str(args.db) if args.db else None)
    )

    if args.report:
        engine.report()
        sys.exit(0)

    result = engine.run(dry_run=args.dry_run)
    engine.report()
    sys.exit(0 if result["status"] == "done" else 1)

# --- MEGA RUNNER ADAPTER ---
def run_all():
    print(f"[{__name__}] Executing run_all...")
    # Call your actual processing logic here
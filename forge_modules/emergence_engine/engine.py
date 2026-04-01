"""
FORGE — Emergence Engine  (forge_modules/emergence_engine/engine.py)
=====================================================================
Detects emerging actor networks and narrative surges using time-based
graph analysis.

ALGORITHM
─────────
Two 24-hour windows are compared for every actor:

    current  window : [now - 24h  →  now]
    baseline window : [now - 48h  →  now - 24h]

For each window the engine counts how many distinct event-links each
actor has (sourced from actor_event_links UNION event_actors, mirroring
the dual-source pattern used by coalition_detector).

Growth metrics:
    growth_rate     = current_count / previous_count
                      (if previous_count == 0 → growth_rate = current_count)
    emergence_score = log(1 + growth_rate) * current_count

Actors are flagged as "emerging" when:
    current_count  >= 3
    growth_rate    >= 2.0

Flagged actors are written to network_emergence.  A full replace is
performed on each run so the table always reflects the latest 24-hour
snapshot.

DATA MODEL
──────────
network_emergence
    id                  INTEGER PK
    actor_id            INTEGER FK → actors
    window_start        TEXT     (ISO-8601, start of current window)
    window_end          TEXT     (ISO-8601, end of current window)
    link_count          INTEGER  (current 24h link count)
    previous_link_count INTEGER  (baseline 24h link count)
    growth_rate         REAL
    emergence_score     REAL
    created_at          TEXT     DEFAULT (datetime('now'))

Returns a pipeline_runs-compatible result dict.
"""

from __future__ import annotations

import math
import sqlite3
import time
import logging
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone, timedelta

log = logging.getLogger("forge.modules.emergence_engine")

DB_PATH = Path(__file__).resolve().parents[2] / "database.db"

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS network_emergence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id            INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    window_start        TEXT    NOT NULL,
    window_end          TEXT    NOT NULL,
    link_count          INTEGER NOT NULL DEFAULT 0,
    previous_link_count INTEGER NOT NULL DEFAULT 0,
    growth_rate         REAL    NOT NULL DEFAULT 0.0,
    emergence_score     REAL    NOT NULL DEFAULT 0.0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_emergence_actor_time
    ON network_emergence (actor_id, window_end);
"""

# ── Main engine function ──────────────────────────────────────────────────────

def run(signal: dict = None, db_path: Path = None) -> dict:
    """
    Public engine entry point.

    Called by:
      - Conclave hook (signal=dict) — no-op, returns None immediately
      - Control Room via POST /api/control/run_emergence
      - module.register() engine registration

    Returns pipeline_runs-compatible result dict.
    """
    _db   = db_path or DB_PATH
    start = time.monotonic()

    now          = datetime.now(timezone.utc)
    window_end   = now
    window_start = now - timedelta(hours=24)
    base_start   = now - timedelta(hours=48)
    base_end     = window_start

    # ISO-8601 strings for SQL and storage
    fmt = "%Y-%m-%d %H:%M:%S"
    window_end_s   = window_end.strftime(fmt)
    window_start_s = window_start.strftime(fmt)
    base_start_s   = base_start.strftime(fmt)
    base_end_s     = base_end.strftime(fmt)

    conn = _open_db(_db)
    try:
        _ensure_schema(conn)

        # ── 1. Determine available source tables ─────────────────────────────
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        # ── 2. Build unified actor-event-time link query ──────────────────────
        # Prefer actor_event_links (timestamped junction) if present.
        # Fall back to event_actors / actor_events joined to events for date.

        def _count_links_per_actor(ts_from: str, ts_to: str) -> dict[int, int]:
            """
            Return {actor_id: link_count} for events whose date falls in
            [ts_from, ts_to).  Uses the richest available source table.
            """
            counts: dict[int, int] = defaultdict(int)

            if "actor_event_links" in tables:
                rows = conn.execute("""
                    SELECT DISTINCT ael.actor_id, ael.event_id
                    FROM   actor_event_links ael
                    WHERE  ael.linked_at >= ? AND ael.linked_at < ?
                """, (ts_from, ts_to)).fetchall()
                for r in rows:
                    counts[r["actor_id"]] += 1

            elif "event_actors" in tables:
                rows = conn.execute("""
                    SELECT DISTINCT ea.actor_id, ea.event_id
                    FROM   event_actors ea
                    JOIN   events e ON e.event_id = ea.event_id
                    WHERE  e.date >= ? AND e.date < ?
                """, (ts_from, ts_to)).fetchall()
                for r in rows:
                    counts[r["actor_id"]] += 1

                # Also pull from actor_events if it exists
                if "actor_events" in tables:
                    rows2 = conn.execute("""
                        SELECT DISTINCT ae.actor_id, ae.event_id
                        FROM   actor_events ae
                        JOIN   events e ON e.event_id = ae.event_id
                        WHERE  e.date >= ? AND e.date < ?
                    """, (ts_from, ts_to)).fetchall()
                    for r in rows2:
                        counts[r["actor_id"]] += 1

            elif "actor_events" in tables:
                rows = conn.execute("""
                    SELECT DISTINCT ae.actor_id, ae.event_id
                    FROM   actor_events ae
                    JOIN   events e ON e.event_id = ae.event_id
                    WHERE  e.date >= ? AND e.date < ?
                """, (ts_from, ts_to)).fetchall()
                for r in rows:
                    counts[r["actor_id"]] += 1

            return counts

        # ── 3. Count links in both windows ───────────────────────────────────
        current_counts  = _count_links_per_actor(window_start_s, window_end_s)
        baseline_counts = _count_links_per_actor(base_start_s,   base_end_s)

        if not current_counts:
            log.info("[emergence_engine] No actor-event links in current window — skipping")
            return {
                "status":          "success",
                "emerging_actors": 0,
                "duration_s":      round(time.monotonic() - start, 2),
            }

        # ── 4. Validate actor IDs against actors table ────────────────────────
        valid_actors = {
            r[0] for r in conn.execute("SELECT actor_id FROM actors").fetchall()
        }

        # ── 5. Compute growth_rate and emergence_score ────────────────────────
        emerging: list[dict] = []

        for actor_id, current_count in current_counts.items():
            if actor_id not in valid_actors:
                continue

            prev_count = baseline_counts.get(actor_id, 0)

            if prev_count == 0:
                growth_rate = float(current_count)
            else:
                growth_rate = current_count / prev_count

            emergence_score = math.log(1 + growth_rate) * current_count

            # ── 6. Flag actors meeting emergence threshold ────────────────────
            if current_count >= 3 and growth_rate >= 2.0:
                emerging.append({
                    "actor_id":            actor_id,
                    "link_count":          current_count,
                    "previous_link_count": prev_count,
                    "growth_rate":         round(growth_rate, 4),
                    "emergence_score":     round(emergence_score, 4),
                })

        log.info(
            f"[emergence_engine] {len(current_counts)} actors in window, "
            f"{len(emerging)} flagged as emerging"
        )

        # ── 7. Write to network_emergence (full replace) ─────────────────────
        conn.execute("DELETE FROM network_emergence")

        for rec in emerging:
            conn.execute("""
                INSERT INTO network_emergence
                    (actor_id, window_start, window_end,
                     link_count, previous_link_count,
                     growth_rate, emergence_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                rec["actor_id"],
                window_start_s,
                window_end_s,
                rec["link_count"],
                rec["previous_link_count"],
                rec["growth_rate"],
                rec["emergence_score"],
            ))

        conn.commit()

        duration = round(time.monotonic() - start, 2)
        log.info(
            f"[emergence_engine] Done — {len(emerging)} emerging actor(s) "
            f"written in {duration}s"
        )

        return {
            "status":          "success",
            "emerging_actors": len(emerging),
            "window_start":    window_start_s,
            "window_end":      window_end_s,
            "duration_s":      duration,
        }

    except Exception as exc:
        log.error(f"[emergence_engine] Engine error: {exc}", exc_info=True)
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return {
            "status":     "error",
            "error":      str(exc),
            "duration_s": round(time.monotonic() - start, 2),
        }
    finally:
        conn.close()


# ── Query helper (used by API route) ─────────────────────────────────────────

def query_emergence(db_path: Path = None) -> list[dict]:
    """
    Return all current emerging actor records with actor name.
    Used by GET /api/intel/emergence.
    """
    _db  = db_path or DB_PATH
    conn = _open_db(_db)
    try:
        _ensure_schema(conn)
        rows = conn.execute("""
            SELECT
                ne.id,
                ne.actor_id,
                a.name          AS actor_name,
                ne.growth_rate,
                ne.link_count   AS current_links,
                ne.previous_link_count,
                ne.emergence_score,
                ne.window_start,
                ne.window_end,
                ne.created_at
            FROM   network_emergence ne
            JOIN   actors a ON a.actor_id = ne.actor_id
            ORDER  BY ne.emergence_score DESC
        """).fetchall()
    finally:
        conn.close()

    return [dict(r) for r in rows]


# ── Internals ─────────────────────────────────────────────────────────────────

def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
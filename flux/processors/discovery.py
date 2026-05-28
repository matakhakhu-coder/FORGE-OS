#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — Discovery Engine  (flux/processors/discovery.py)
═════════════════════════════════════════════════════════════
Analyses the flux_tag_cooccurrence table to surface Latent Seeds —
tags that consistently co-occur with known seed targets and may
represent emerging clusters worth monitoring.

Architecture
────────────
  run(db_path, dry_run, threshold)
      Main entry point. Returns a summary dict.

  Phase 1 — Co-occurrence rates
      For every (seed_tag, co_tag) pair in flux_tag_cooccurrence,
      compute the Jaccard similarity and conditional co-occurrence rate
      across all stored pulses. Tags whose rate exceeds `threshold`
      (default 0.30) are candidates for promotion.

  Phase 2 — Velocity scoring
      Compare the most recent pulse's co_tag count against the previous
      pulse's count for the same seed. Growth velocity = current / previous.
      Tags that double (velocity >= 2.0) trigger an immediate gravity
      escalation on recent signals carrying that tag.

  Phase 3 — Latent seed promotion
      Candidates are upserted into flux_latent_seeds with their
      jaccard_score, velocity, discovery_depth, and parent_seed.
      discovery_depth is inherited from the parent: a tag discovered from
      a depth-0 seed is depth-1; from a depth-1 seed is depth-2.
      Tags already at depth >= FLUX_MAX_DISCOVERY_DEPTH are skipped.

Guardrails
──────────
  MIN_VELOCITY_COUNT = 3   — a tag must appear >= 3 times total before
                              velocity escalation fires. Prevents 1→2
                              count noise from triggering alerts.

Usage
─────
  python flux/processors/discovery.py
  python flux/processors/discovery.py --dry-run
  python flux/processors/discovery.py --threshold 0.40
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths & sys.path bootstrap ────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("FORGE_DB", str(BASE_DIR / "database.db")))

# ── Thresholds ────────────────────────────────────────────────────────────────

CO_OCCURRENCE_THRESHOLD  = float(os.environ.get("FLUX_CO_THRESHOLD",    "0.30"))
VELOCITY_ESCALATE_AT     = float(os.environ.get("FLUX_VELOCITY_THRESH", "2.0"))
MIN_VELOCITY_COUNT       = int(  os.environ.get("FLUX_MIN_VELOCITY_COUNT", "3"))
FLUX_MAX_DISCOVERY_DEPTH = int(  os.environ.get("FLUX_MAX_DISCOVERY_DEPTH", "2"))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [discovery] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
log = logging.getLogger("forge.flux.discovery")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


# ─────────────────────────────────────────────────────────────────────────────
# Schema guard
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create FLUX discovery tables if not present — idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flux_tag_cooccurrence (
            pulse_id    TEXT    NOT NULL,
            pulse_ts    TEXT    NOT NULL,
            seed_tag    TEXT    NOT NULL,
            co_tag      TEXT    NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pulse_id, seed_tag, co_tag)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ftc_seed ON flux_tag_cooccurrence(seed_tag)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ftc_co  ON flux_tag_cooccurrence(co_tag)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flux_latent_seeds (
            tag             TEXT    PRIMARY KEY,
            parent_seed     TEXT,
            discovery_depth INTEGER NOT NULL DEFAULT 1,
            jaccard_score   REAL    NOT NULL DEFAULT 0.0,
            velocity        REAL    NOT NULL DEFAULT 1.0,
            total_count     INTEGER NOT NULL DEFAULT 0,
            first_seen      TEXT    NOT NULL,
            last_seen       TEXT    NOT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Co-occurrence rates
# ─────────────────────────────────────────────────────────────────────────────

def _get_seed_pairs(conn: sqlite3.Connection) -> list[dict]:
    """
    Aggregate flux_tag_cooccurrence across all pulses.

    Returns rows of:
        seed_tag, co_tag,
        co_count     (total tweets with both seed and co_tag),
        seed_total   (total tweets collected for this seed)

    The "__total__" sentinel row written by x_pulse.py provides seed_total.
    """
    rows = conn.execute("""
        SELECT
            co.seed_tag,
            co.co_tag,
            SUM(co.count)                                   AS co_count,
            COALESCE(tot.seed_total, 0)                     AS seed_total
        FROM  flux_tag_cooccurrence co
        LEFT  JOIN (
            SELECT seed_tag, SUM(count) AS seed_total
            FROM   flux_tag_cooccurrence
            WHERE  co_tag = '__total__'
            GROUP  BY seed_tag
        ) tot ON tot.seed_tag = co.seed_tag
        WHERE  co.co_tag != '__total__'
        GROUP  BY co.seed_tag, co.co_tag
        HAVING seed_total > 0
    """).fetchall()
    return [dict(r) for r in rows]


def _jaccard(co_count: int, seed_total: int, co_total: int) -> float:
    """
    Jaccard similarity: |A ∩ B| / |A ∪ B|
        A = tweets with seed, B = tweets with co_tag
        |A ∪ B| = seed_total + co_total - co_count
    """
    union = seed_total + co_total - co_count
    if union <= 0:
        return 0.0
    return co_count / union


def _get_co_tag_totals(conn: sqlite3.Connection) -> dict[str, int]:
    """Total appearances of each co_tag across all seeds and pulses."""
    rows = conn.execute("""
        SELECT co_tag, SUM(count) AS total
        FROM   flux_tag_cooccurrence
        WHERE  co_tag != '__total__'
        GROUP  BY co_tag
    """).fetchall()
    return {r["co_tag"]: r["total"] for r in rows}


def _get_seed_depths(conn: sqlite3.Connection) -> dict[str, int]:
    """
    Returns discovery_depth for all known latent seeds.
    Seeds not in flux_latent_seeds are treated as depth 0 (original seeds).
    """
    rows = conn.execute(
        "SELECT tag, discovery_depth FROM flux_latent_seeds"
    ).fetchall()
    return {r["tag"]: r["discovery_depth"] for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Velocity scoring
# ─────────────────────────────────────────────────────────────────────────────

def _get_pulse_sequence(conn: sqlite3.Connection) -> list[str]:
    """Return pulse_ids ordered oldest → newest."""
    rows = conn.execute("""
        SELECT DISTINCT pulse_id, MIN(pulse_ts) AS ts
        FROM   flux_tag_cooccurrence
        GROUP  BY pulse_id
        ORDER  BY ts ASC
    """).fetchall()
    return [r["pulse_id"] for r in rows]


def _compute_velocity(
    conn: sqlite3.Connection,
    co_tag: str,
    pulse_sequence: list[str],
) -> float:
    """
    Growth velocity: (latest_pulse_count) / (previous_pulse_count).

    Returns 1.0 (neutral) if fewer than 2 pulses exist or the tag has
    not appeared in the two most recent pulses.
    Requires total_count >= MIN_VELOCITY_COUNT before returning > 1.0.
    """
    if len(pulse_sequence) < 2:
        return 1.0

    latest_pid = pulse_sequence[-1]
    prev_pid   = pulse_sequence[-2]

    latest_row = conn.execute(
        "SELECT SUM(count) AS n FROM flux_tag_cooccurrence "
        "WHERE co_tag = ? AND pulse_id = ? AND co_tag != '__total__'",
        (co_tag, latest_pid),
    ).fetchone()

    prev_row = conn.execute(
        "SELECT SUM(count) AS n FROM flux_tag_cooccurrence "
        "WHERE co_tag = ? AND pulse_id = ? AND co_tag != '__total__'",
        (co_tag, prev_pid),
    ).fetchone()

    latest_n = (latest_row["n"] or 0) if latest_row else 0
    prev_n   = (prev_row["n"]   or 0) if prev_row   else 0

    # Minimum count guard — prevents noise velocity spikes
    total_row = conn.execute(
        "SELECT SUM(count) AS n FROM flux_tag_cooccurrence "
        "WHERE co_tag = ? AND co_tag != '__total__'",
        (co_tag,),
    ).fetchone()
    total_n = (total_row["n"] or 0) if total_row else 0
    if total_n < MIN_VELOCITY_COUNT:
        return 1.0

    if prev_n == 0:
        return float(latest_n) if latest_n > 0 else 1.0
    return round(latest_n / prev_n, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Gravity escalation for velocity-spiking tags
# ─────────────────────────────────────────────────────────────────────────────

def _escalate_gravity(
    conn: sqlite3.Connection,
    tag: str,
    dry_run: bool,
) -> int:
    """
    Boost relevance_score on signals that mention this tag in socint_tags
    or metadata_json. Caps at 1.0.

    Returns count of signals escalated.
    """
    like_pattern = f'%"{tag}"%'
    candidates = conn.execute(
        """
        SELECT signal_id, relevance_score
        FROM   signals
        WHERE  (socint_tags    LIKE ? OR metadata_json LIKE ?)
          AND  timestamp >= datetime('now', '-1 hour')
        """,
        (like_pattern, like_pattern),
    ).fetchall()

    if not candidates:
        return 0

    if dry_run:
        log.info(
            "[DRY-RUN] velocity escalation: tag=%s  signals=%d",
            tag, len(candidates),
        )
        return len(candidates)

    updated = 0
    for row in candidates:
        new_score = min(1.0, round((row["relevance_score"] or 0.5) * 1.35, 4))
        conn.execute(
            "UPDATE signals SET relevance_score = ? WHERE signal_id = ?",
            (new_score, row["signal_id"]),
        )
        updated += 1

    return updated


# ─────────────────────────────────────────────────────────────────────────────
# Upsert — flux_latent_seeds
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_latent_seeds(
    conn: sqlite3.Connection,
    candidates: list[dict],
    dry_run: bool,
) -> int:
    """
    Insert or update latent seed records.
    Preserves first_seen on conflict; updates all scored fields.
    """
    now = _ts()
    written = 0
    for c in candidates:
        if dry_run:
            log.info(
                "[DRY-RUN] latent seed: tag=%-20s  jaccard=%.3f  velocity=%.2f  depth=%d",
                c["tag"], c["jaccard_score"], c["velocity"], c["discovery_depth"],
            )
            continue
        conn.execute(
            """
            INSERT INTO flux_latent_seeds
                (tag, parent_seed, discovery_depth, jaccard_score, velocity,
                 total_count, first_seen, last_seen, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(tag) DO UPDATE SET
                parent_seed     = excluded.parent_seed,
                jaccard_score   = excluded.jaccard_score,
                velocity        = excluded.velocity,
                total_count     = excluded.total_count,
                last_seen       = excluded.last_seen,
                is_active       = 1
            """,
            (
                c["tag"], c["parent_seed"], c["discovery_depth"],
                c["jaccard_score"], c["velocity"], c["total_count"],
                now, now,
            ),
        )
        written += 1
    return written


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(
    db_path:   Path = DB_PATH,
    dry_run:   bool = False,
    threshold: float = CO_OCCURRENCE_THRESHOLD,
) -> dict:
    """
    Execute the full discovery pass.

    Returns a summary dict:
        candidates_found : int — pairs that exceeded threshold
        seeds_written    : int — rows upserted into flux_latent_seeds
        escalations      : int — signals whose relevance_score was boosted
        dry_run          : bool
    """
    log.info("=== FLUX Discovery Engine ===")
    log.info("DB         : %s", db_path)
    log.info("Threshold  : %.2f  (velocity escalation >= %.1f, min_count=%d)",
             threshold, VELOCITY_ESCALATE_AT, MIN_VELOCITY_COUNT)
    log.info("Dry run    : %s", dry_run)

    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn)

        # ── Phase 1: co-occurrence rates ──────────────────────────────────────
        log.info("=== Phase 1: Co-occurrence analysis ===")
        pairs       = _get_seed_pairs(conn)
        co_totals   = _get_co_tag_totals(conn)
        seed_depths = _get_seed_depths(conn)

        if not pairs:
            log.info("No co-occurrence data — run x_pulse.py first.")
            return {
                "status": "empty", "candidates_found": 0,
                "seeds_written": 0, "escalations": 0, "dry_run": dry_run,
            }

        log.info("Pairs loaded: %d", len(pairs))

        # ── Phase 2: velocity scoring ─────────────────────────────────────────
        log.info("=== Phase 2: Velocity scoring ===")
        pulse_seq = _get_pulse_sequence(conn)
        log.info("Pulses in history: %d", len(pulse_seq))

        # ── Build candidate list ──────────────────────────────────────────────
        candidates: list[dict] = []
        escalations = 0

        for pair in pairs:
            seed_tag  = pair["seed_tag"]
            co_tag    = pair["co_tag"]
            co_count  = pair["co_count"]
            seed_total = pair["seed_total"]

            # Co-occurrence rate (conditional probability)
            co_rate = co_count / seed_total if seed_total else 0.0
            if co_rate < threshold:
                continue

            # Jaccard similarity
            co_total_count = co_totals.get(co_tag, co_count)
            j_score = _jaccard(co_count, seed_total, co_total_count)

            # Velocity
            velocity = _compute_velocity(conn, co_tag, pulse_seq)

            # Depth: inherit parent depth + 1; skip if already at max
            parent_depth = seed_depths.get(seed_tag, 0)
            new_depth    = parent_depth + 1
            if new_depth > FLUX_MAX_DISCOVERY_DEPTH:
                continue

            candidates.append({
                "tag":            co_tag,
                "parent_seed":    seed_tag,
                "discovery_depth": new_depth,
                "jaccard_score":  round(j_score,  4),
                "velocity":       round(velocity, 4),
                "total_count":    int(co_count),
            })

            # Velocity escalation — fires on ≥2× growth with minimum count gate
            if velocity >= VELOCITY_ESCALATE_AT:
                log.info(
                    "Velocity spike: tag=%-20s  velocity=%.2f → escalating gravity",
                    co_tag, velocity,
                )
                n = _escalate_gravity(conn, co_tag, dry_run)
                escalations += n

        log.info("Candidates above threshold: %d", len(candidates))

        # ── Phase 3: upsert latent seeds ──────────────────────────────────────
        log.info("=== Phase 3: Promoting latent seeds ===")
        written = _upsert_latent_seeds(conn, candidates, dry_run)

        if not dry_run:
            conn.commit()

    finally:
        conn.close()

    summary = {
        "status":            "done",
        "candidates_found":  len(candidates),
        "seeds_written":     written,
        "escalations":       escalations,
        "pulses_analysed":   len(pulse_seq),
        "dry_run":           dry_run,
    }
    log.info("Complete: %s", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE FLUX — Discovery Engine"
    )
    parser.add_argument(
        "--db",        type=Path,  default=None,
        help="Override database path",
    )
    parser.add_argument(
        "--dry-run",   action="store_true",
        help="Analyse without writing to flux_latent_seeds",
    )
    parser.add_argument(
        "--threshold", type=float, default=CO_OCCURRENCE_THRESHOLD,
        help=f"Co-occurrence rate threshold (default {CO_OCCURRENCE_THRESHOLD})",
    )
    args = parser.parse_args()

    result = run(
        db_path   = Path(args.db) if args.db else DB_PATH,
        dry_run   = args.dry_run,
        threshold = args.threshold,
    )
    sys.exit(0 if result["status"] in ("done", "empty") else 1)

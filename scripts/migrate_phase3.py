#!/usr/bin/env python
"""
scripts/migrate_phase3.py — Phase 3 Shadow Vault Schema Migration
==================================================================

Zero-downtime migration strategy:
  1. Create signals_new with extended schema (source_reliability, info_credibility)
  2. Create provenance table (exhibit stamps)
  3. Create pii_audits table
  4. Install AFTER INSERT trigger on signals → dual-writes to signals_new
  5. Backfill existing signals into signals_new
  6. Report

All DDL runs inside individual transactions with IF NOT EXISTS guards,
making this script fully idempotent — safe to re-run.

Usage
─────
  python scripts/migrate_phase3.py
  python scripts/migrate_phase3.py --db /path/to/database.db
  python scripts/migrate_phase3.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── DB resolution ─────────────────────────────────────────────────────────────

def _resolve_db(override=None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1] / "database.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ── DDL statements ────────────────────────────────────────────────────────────

# signals_new: full signals schema + Admiralty columns
_DDL_SIGNALS_NEW = """
CREATE TABLE IF NOT EXISTS signals_new (
    signal_id            TEXT    PRIMARY KEY,
    source               TEXT,
    external_id          TEXT    UNIQUE,
    title                TEXT,
    content              TEXT,
    lat                  REAL,
    lng                  REAL,
    timestamp            TEXT,
    status               TEXT    DEFAULT 'raw',
    metadata_json        TEXT,
    cluster_id           INTEGER,
    is_priority          INTEGER DEFAULT 0,
    confidence_score     REAL,
    source_artifact_id   INTEGER,
    stream               TEXT,
    relevance_score      REAL    DEFAULT 0.0,
    source_type          TEXT    DEFAULT 'live',
    gravity_score        REAL,
    conclave_meta        TEXT,
    -- Phase 3 Admiralty columns
    source_reliability   CHAR(1) CHECK(source_reliability IN ('A','B','C','D','E','F')),
    info_credibility     INTEGER CHECK(info_credibility BETWEEN 1 AND 6),
    admiralty_weight     REAL    GENERATED ALWAYS AS (
        CASE source_reliability
            WHEN 'A' THEN 1.00
            WHEN 'B' THEN 0.80
            WHEN 'C' THEN 0.60
            WHEN 'D' THEN 0.40
            WHEN 'E' THEN 0.10
            ELSE 0.50
        END *
        CASE info_credibility
            WHEN 1 THEN 1.00
            WHEN 2 THEN 0.80
            WHEN 3 THEN 0.60
            WHEN 4 THEN 0.35
            WHEN 5 THEN 0.10
            ELSE 0.50
        END
    ) VIRTUAL,
    created_at           TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""

_DDL_SIGNALS_NEW_IDX = """
CREATE INDEX IF NOT EXISTS idx_signals_new_source_reliability
    ON signals_new(source_reliability);
CREATE INDEX IF NOT EXISTS idx_signals_new_info_credibility
    ON signals_new(info_credibility);
CREATE INDEX IF NOT EXISTS idx_signals_new_timestamp
    ON signals_new(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_new_gravity
    ON signals_new(gravity_score DESC);
"""

# provenance: exhibit stamps for court-ready artifact evidence
_DDL_PROVENANCE = """
CREATE TABLE IF NOT EXISTS provenance (
    provenance_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id      INTEGER NOT NULL,
    case_id          INTEGER,
    signal_id        TEXT,
    signature        TEXT    NOT NULL,
    captured_at      TEXT    NOT NULL,
    dev_hash         TEXT    NOT NULL,
    case_hash        TEXT    NOT NULL,
    component        TEXT    DEFAULT 'manual',
    detail_json      TEXT,
    created_at       TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);
"""

_DDL_PROVENANCE_IDX = """
CREATE INDEX IF NOT EXISTS idx_provenance_artifact
    ON provenance(artifact_id);
CREATE INDEX IF NOT EXISTS idx_provenance_case
    ON provenance(case_id);
CREATE INDEX IF NOT EXISTS idx_provenance_captured_at
    ON provenance(captured_at DESC);
"""

# pii_audits: PII detection log for signals and artifacts
_DDL_PII_AUDITS = """
CREATE TABLE IF NOT EXISTS pii_audits (
    audit_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type      TEXT    NOT NULL CHECK(entity_type IN ('signal','artifact')),
    entity_id        TEXT    NOT NULL,
    pii_types        TEXT    NOT NULL,   -- JSON array: ["name","id_number","phone"]
    action_taken     TEXT    NOT NULL DEFAULT 'flagged',
                                         -- flagged | redacted | suppressed | cleared
    reviewed_by      TEXT,               -- analyst username or NULL for automated
    detail_json      TEXT,
    created_at       TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    resolved_at      TEXT
);
"""

_DDL_PII_AUDITS_IDX = """
CREATE INDEX IF NOT EXISTS idx_pii_audits_entity
    ON pii_audits(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_pii_audits_action
    ON pii_audits(action_taken);
CREATE INDEX IF NOT EXISTS idx_pii_audits_created_at
    ON pii_audits(created_at DESC);
"""

# AFTER INSERT trigger: dual-write from signals → signals_new
# Inserts with source_reliability='F' (unknown) and info_credibility=6 (unknown)
# as defaults; the admiralty backfill job will update them based on domain.
_DDL_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_signals_dual_write
AFTER INSERT ON signals
BEGIN
    INSERT OR IGNORE INTO signals_new (
        signal_id, source, external_id, title, content,
        lat, lng, timestamp, status, metadata_json,
        cluster_id, is_priority, confidence_score,
        source_artifact_id, stream, relevance_score,
        source_type, gravity_score, conclave_meta,
        source_reliability, info_credibility
    ) VALUES (
        NEW.signal_id, NEW.source, NEW.external_id, NEW.title, NEW.content,
        NEW.lat, NEW.lng, NEW.timestamp, NEW.status, NEW.metadata_json,
        NEW.cluster_id, NEW.is_priority, NEW.confidence_score,
        NEW.source_artifact_id, NEW.stream, NEW.relevance_score,
        NEW.source_type, NEW.gravity_score, NEW.conclave_meta,
        'F', 6
    );
END;
"""


# ── Backfill ──────────────────────────────────────────────────────────────────

def _backfill(conn: sqlite3.Connection, batch_size: int = 5000) -> int:
    """
    Copy all existing signals rows into signals_new.
    Uses INSERT OR IGNORE so already-migrated rows are skipped.
    Processes in batches to avoid SQLite write-lock stalls.
    Returns total rows inserted.
    """
    total_inserted = 0
    offset = 0

    while True:
        rows = conn.execute(
            "SELECT signal_id FROM signals LIMIT ? OFFSET ?",
            (batch_size, offset)
        ).fetchall()
        if not rows:
            break

        ids = [r["signal_id"] for r in rows]
        ph  = ",".join("?" * len(ids))

        conn.execute(f"""
            INSERT OR IGNORE INTO signals_new (
                signal_id, source, external_id, title, content,
                lat, lng, timestamp, status, metadata_json,
                cluster_id, is_priority, confidence_score,
                source_artifact_id, stream, relevance_score,
                source_type, gravity_score, conclave_meta,
                source_reliability, info_credibility
            )
            SELECT
                signal_id, source, external_id, title, content,
                lat, lng, timestamp, status, metadata_json,
                cluster_id, is_priority, confidence_score,
                source_artifact_id, stream, relevance_score,
                source_type, gravity_score, conclave_meta,
                'F', 6
            FROM signals
            WHERE signal_id IN ({ph})
        """, ids)
        conn.commit()

        batch_inserted = conn.execute("SELECT changes()").fetchone()[0]
        total_inserted += batch_inserted
        offset += batch_size

        print(f"  Backfill: {offset} signals processed, {total_inserted} inserted…")

        if len(rows) < batch_size:
            break

    return total_inserted


# ── Main ──────────────────────────────────────────────────────────────────────

def run(db_path: Path, dry_run: bool = False) -> dict:
    start = time.monotonic()

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _connect(db_path)
    results: dict = {}

    print(f"[migrate_phase3] Database: {db_path}")
    print(f"[migrate_phase3] Dry-run: {dry_run}")
    print()

    # ── Step 1: signals_new ───────────────────────────────────────────────────
    print("Step 1/4 — Creating signals_new table…")
    if not dry_run:
        conn.execute(_DDL_SIGNALS_NEW)
        for stmt in _DDL_SIGNALS_NEW_IDX.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM signals_new").fetchone()[0]
        print(f"  signals_new exists — {count} rows currently.")
        results["signals_new"] = "created"
    else:
        print("  [DRY RUN] Would create signals_new + 4 indexes.")
        results["signals_new"] = "dry_run"

    # ── Step 2: provenance ────────────────────────────────────────────────────
    print("\nStep 2/4 — Creating provenance table…")
    if not dry_run:
        conn.execute(_DDL_PROVENANCE)
        for stmt in _DDL_PROVENANCE_IDX.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        print("  provenance table created (or already exists).")
        results["provenance"] = "created"
    else:
        print("  [DRY RUN] Would create provenance + 3 indexes.")
        results["provenance"] = "dry_run"

    # ── Step 3: pii_audits ────────────────────────────────────────────────────
    print("\nStep 3/4 — Creating pii_audits table…")
    if not dry_run:
        conn.execute(_DDL_PII_AUDITS)
        for stmt in _DDL_PII_AUDITS_IDX.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        print("  pii_audits table created (or already exists).")
        results["pii_audits"] = "created"
    else:
        print("  [DRY RUN] Would create pii_audits + 3 indexes.")
        results["pii_audits"] = "dry_run"

    # ── Step 4: trigger + backfill ────────────────────────────────────────────
    print("\nStep 4/4 — Installing dual-write trigger + backfilling…")
    if not dry_run:
        conn.execute(_DDL_TRIGGER)
        conn.commit()
        print("  Trigger trg_signals_dual_write installed.")

        live_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        print(f"  Backfilling {live_count:,} signals into signals_new…")
        inserted = _backfill(conn, batch_size=5000)
        print(f"  Backfill complete — {inserted:,} rows inserted.")
        results["trigger"]   = "installed"
        results["backfilled"] = inserted
    else:
        live_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        print(f"  [DRY RUN] Would install trigger and backfill {live_count:,} signals.")
        results["trigger"]   = "dry_run"
        results["backfilled"] = 0

    # ── Log to pipeline_runs ──────────────────────────────────────────────────
    duration = round(time.monotonic() - start, 2)
    if not dry_run:
        try:
            conn.execute("""
                INSERT INTO pipeline_runs
                    (component, status, records_in, records_out, duration_s, detail_json)
                VALUES ('migrate_phase3', 'success', ?, ?, ?, ?)
            """, (
                results.get("backfilled", 0),
                results.get("backfilled", 0),
                duration,
                json.dumps(results),
            ))
            conn.commit()
        except Exception as e:
            print(f"  Warning: pipeline_runs log failed: {e}")

    conn.close()
    results["duration_s"] = duration

    print(f"\n[migrate_phase3] Done in {duration}s")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Phase 3 Shadow Vault Migration"
    )
    parser.add_argument("--db",      type=str, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without writing anything")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    result  = run(db_path, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    sys.exit(0)

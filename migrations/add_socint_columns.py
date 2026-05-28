#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — Phase A Schema Migration
══════════════════════════════════════
Adds all SOCINT infrastructure to the live database.

New tables
──────────
  socint_signals    — FLUX-native signal store (X posts, per-author capture)
  socint_resonance  — Pairwise stylometric similarity scores between actors

New columns
───────────
  actors.socint_profile  TEXT  — JSON corpus: X handles, aliases, text samples
  signals.socint_tags    TEXT  — JSON array of SOCINT-derived behavioural tags
  signals.socint_resonance REAL — Highest resonance score linked to this signal

Design rules
────────────
  • Idempotent: PRAGMA table_info guard on every column, IF NOT EXISTS on tables.
  • Pre-flight: refuses to run while any pipeline_jobs row is status='running'.
  • timeout=60 on connection — matches CLAUDE.md DB timeout spec.
  • try/finally: conn.close() — never leaks a connection.
  • Partial index on signals.socint_resonance (IS NOT NULL) — avoids indexing
    the ~99 % of rows that will never be SOCINT-enriched.
  • FK enforcement ON so cascades on socint_resonance are live immediately.

Usage
─────
    python migrations/add_socint_columns.py

    # Dry-run (prints planned operations, touches nothing)
    DRY_RUN=1 python migrations/add_socint_columns.py
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [socint-migration] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [socint-migration] WARN  {msg}", flush=True)
def ok(msg: str)   -> None: print(f"[{_ts()}] [socint-migration] OK    {msg}", flush=True)
def skip(msg: str) -> None: print(f"[{_ts()}] [socint-migration] SKIP  {msg} (already exists)", flush=True)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone())


# ── Pre-flight ────────────────────────────────────────────────────────────────

def _preflight(conn: sqlite3.Connection) -> None:
    """Abort if any pipeline job is actively running. Schema locks during
    an active ingest write burst risk 'database is locked' errors."""
    try:
        running = conn.execute(
            "SELECT job_id, job_key FROM pipeline_jobs WHERE status = 'running'"
        ).fetchall()
    except sqlite3.OperationalError:
        # pipeline_jobs table may not exist in a fresh DB — that is fine
        return
    if running:
        ids = ", ".join(str(r[0]) for r in running)
        keys = ", ".join(r[1] for r in running)
        warn(f"Active pipeline jobs detected: [{ids}] ({keys})")
        warn("Stop all running jobs before migrating. Aborting.")
        sys.exit(1)


# ── DDL blocks ────────────────────────────────────────────────────────────────

_SOCINT_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS socint_signals (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    source        TEXT     NOT NULL DEFAULT 'x_pulse',
    actor_id      INTEGER  REFERENCES actors(actor_id) ON DELETE SET NULL,
    signal_id     TEXT     REFERENCES signals(signal_id) ON DELETE SET NULL,
    content       TEXT     NOT NULL,
    metadata_json TEXT     DEFAULT NULL,
    timestamp     TEXT     NOT NULL DEFAULT (datetime('now'))
)
"""

_SOCINT_RESONANCE_DDL = """
CREATE TABLE IF NOT EXISTS socint_resonance (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    actor_a       INTEGER  NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    actor_b       INTEGER  NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    score         REAL     NOT NULL DEFAULT 0.0,
    features_json TEXT     DEFAULT NULL,
    updated_at    TEXT     NOT NULL DEFAULT (datetime('now')),
    UNIQUE(actor_a, actor_b),
    CHECK(score >= 0.0 AND score <= 1.0),
    CHECK(actor_a < actor_b)
)
"""

# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    if not DB_PATH.exists():
        warn(f"Database not found at {DB_PATH}. Run: python app.py --init-db")
        sys.exit(1)

    log(f"Database : {DB_PATH}")
    log(f"Dry run  : {dry_run}")

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── Pre-flight ──────────────────────────────────────────────────────
        _preflight(conn)

        # ── Table: socint_signals ───────────────────────────────────────────
        if not _table_exists(conn, "socint_signals"):
            log("Creating table socint_signals...")
            if not dry_run:
                conn.execute(_SOCINT_SIGNALS_DDL)
            ok("socint_signals created")
        else:
            skip("socint_signals")

        # ── Table: socint_resonance ─────────────────────────────────────────
        if not _table_exists(conn, "socint_resonance"):
            log("Creating table socint_resonance...")
            if not dry_run:
                conn.execute(_SOCINT_RESONANCE_DDL)
            ok("socint_resonance created")
        else:
            skip("socint_resonance")

        # ── Column: actors.socint_profile ───────────────────────────────────
        if not _column_exists(conn, "actors", "socint_profile"):
            log("Adding actors.socint_profile TEXT DEFAULT NULL...")
            if not dry_run:
                conn.execute(
                    "ALTER TABLE actors ADD COLUMN socint_profile TEXT DEFAULT NULL"
                )
            ok("actors.socint_profile added")
        else:
            skip("actors.socint_profile")

        # ── Column: signals.socint_tags ─────────────────────────────────────
        if not _column_exists(conn, "signals", "socint_tags"):
            log("Adding signals.socint_tags TEXT DEFAULT NULL...")
            if not dry_run:
                conn.execute(
                    "ALTER TABLE signals ADD COLUMN socint_tags TEXT DEFAULT NULL"
                )
            ok("signals.socint_tags added")
        else:
            skip("signals.socint_tags")

        # ── Column: signals.socint_resonance ────────────────────────────────
        if not _column_exists(conn, "signals", "socint_resonance"):
            log("Adding signals.socint_resonance REAL DEFAULT NULL...")
            if not dry_run:
                conn.execute(
                    "ALTER TABLE signals ADD COLUMN socint_resonance REAL DEFAULT NULL"
                )
            ok("signals.socint_resonance added")
        else:
            skip("signals.socint_resonance")

        # ── Indexes ─────────────────────────────────────────────────────────
        # Partial index — only rows that have been SOCINT-enriched
        if not _index_exists(conn, "idx_signals_socint_resonance"):
            log("Creating partial index idx_signals_socint_resonance...")
            if not dry_run:
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_signals_socint_resonance
                    ON signals(socint_resonance)
                    WHERE socint_resonance IS NOT NULL
                """)
            ok("idx_signals_socint_resonance created")
        else:
            skip("idx_signals_socint_resonance")

        if not _index_exists(conn, "idx_socint_signals_actor"):
            log("Creating index idx_socint_signals_actor...")
            if not dry_run:
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_socint_signals_actor
                    ON socint_signals(actor_id)
                """)
            ok("idx_socint_signals_actor created")
        else:
            skip("idx_socint_signals_actor")

        if not _index_exists(conn, "idx_socint_resonance_score"):
            log("Creating index idx_socint_resonance_score...")
            if not dry_run:
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_socint_resonance_score
                    ON socint_resonance(score DESC)
                """)
            ok("idx_socint_resonance_score created")
        else:
            skip("idx_socint_resonance_score")

        # ── Commit ──────────────────────────────────────────────────────────
        if not dry_run:
            conn.commit()
            log("Migration committed.")
        else:
            log("Dry run complete — no changes written.")

    finally:
        conn.close()

    log("Phase A complete. FORGE is ready for FLUX.")


if __name__ == "__main__":
    dry_run = os.environ.get("DRY_RUN", "0").strip() in ("1", "true", "yes")
    run(dry_run=dry_run)

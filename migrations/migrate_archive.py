"""
FORGE — Archive Migration
=========================
Run once to add archive tables to your existing database.

Usage:
    python migrate_archive.py

Safe to run multiple times — all statements use CREATE TABLE IF NOT EXISTS.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"

ARCHIVE_SCHEMA = [

    # ── signals_archive ────────────────────────────────────────────────────
    # Identical columns to signals, minus the UNIQUE constraint on external_id
    # (same signal could be archived under multiple cases over time).
    # Adds archived_at and archived_case_id for provenance.
    """
    CREATE TABLE IF NOT EXISTS signals_archive (
        signal_id          TEXT    NOT NULL,
        source             TEXT    NOT NULL,
        external_id        TEXT    NOT NULL,
        title              TEXT    NOT NULL,
        content            TEXT,
        lat                REAL,
        lng                REAL,
        timestamp          DATETIME,
        status             TEXT,
        metadata_json      TEXT,
        cluster_id         TEXT,
        is_priority        INTEGER NOT NULL DEFAULT 0,
        confidence_score   REAL,
        source_artifact_id INTEGER,
        stream             TEXT,
        relevance_score    REAL,
        source_type        TEXT,
        archived_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        archived_case_id   INTEGER NOT NULL,
        PRIMARY KEY (signal_id, archived_case_id)
    )
    """,

    # ── events_archive ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS events_archive (
        event_id         INTEGER NOT NULL,
        title            TEXT    NOT NULL,
        summary          TEXT,
        date             TEXT,
        location         TEXT,
        latitude         REAL,
        longitude        REAL,
        category         TEXT,
        source_type      TEXT,
        created_at       TEXT,
        archived_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        archived_case_id INTEGER NOT NULL,
        PRIMARY KEY (event_id, archived_case_id)
    )
    """,

    # ── artifacts_archive ──────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS artifacts_archive (
        artifact_id        INTEGER NOT NULL,
        title              TEXT    NOT NULL,
        description        TEXT,
        type               TEXT,
        date               TEXT,
        location           TEXT,
        latitude           REAL,
        longitude          REAL,
        tags               TEXT,
        source             TEXT,
        source_type        TEXT,
        file_path          TEXT,
        thumbnail          TEXT,
        event_id           INTEGER,
        created_at         TEXT,
        raw_text_cache     TEXT,
        processing_status  TEXT,
        file_hash_sha256   TEXT,
        file_hash_md5      TEXT,
        file_size_bytes    INTEGER,
        exif_json          TEXT,
        gps_lat            REAL,
        gps_lng            REAL,
        device_make        TEXT,
        device_model       TEXT,
        exif_datetime      TEXT,
        archived_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        archived_case_id   INTEGER NOT NULL,
        PRIMARY KEY (artifact_id, archived_case_id)
    )
    """,

    # ── Indexes for fast case-scoped queries on archive tables ─────────────
    """
    CREATE INDEX IF NOT EXISTS idx_signals_archive_case
        ON signals_archive (archived_case_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_signals_archive_source
        ON signals_archive (source, timestamp)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_archive_case
        ON events_archive (archived_case_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_artifacts_archive_case
        ON artifacts_archive (archived_case_id)
    """,
]


def run():
    print(f"[archive-migrate] Target database: {DB_PATH}")
    if not DB_PATH.exists():
        print("[archive-migrate] ERROR: database.db not found. Run --init-db first.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    for stmt in ARCHIVE_SCHEMA:
        conn.execute(stmt)

    conn.commit()
    conn.close()
    print("[archive-migrate] Done — signals_archive, events_archive, "
          "artifacts_archive created (idempotent).")


if __name__ == "__main__":
    run()
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from flask import g

from core.db.connection import get_connection

BASE_DIR  = Path(__file__).resolve().parent.parent.parent
DB_PATH   = BASE_DIR / "database.db"
MEDIA_DIR = BASE_DIR / "media"

MEDIA_SUBDIRS = ["images", "videos", "documents", "audio", "actors"]
ACTOR_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

ADMIN_PASSWORD = os.environ.get("FORGE_ADMIN_PASSWORD", "")

SOURCE_META = {
    "verified":    {"label": "Verified",         "colour": "#2d7a4f"},
    "unverified":  {"label": "Unverified",        "colour": "#b07d2a"},
    "government":  {"label": "Government Source", "colour": "#1e3a6e"},
    "leaked":      {"label": "Anonymous Leak",    "colour": "#8b1a1a"},
    "citizen":     {"label": "Citizen Footage",   "colour": "#4a4a4a"},
    "media":       {"label": "Media Report",      "colour": "#1a4a6e"},
}

_VALID_ACTOR_TYPES = [
    "person", "institution", "media", "movement", "government",
    "location", "political_party", "organization", "other",
    "paramilitary", "unknown",
]


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = get_connection()
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL;")
        g.db.execute("PRAGMA foreign_keys=ON;")
    return g.db


_PIPELINE_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key      TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    stage        TEXT,
    progress     REAL    DEFAULT 0.0,
    message      TEXT,
    pid          INTEGER,
    records_in   INTEGER DEFAULT 0,
    records_out  INTEGER DEFAULT 0,
    started_at   TEXT,
    updated_at   TEXT,
    finished_at  TEXT
)
"""

_ALLOWED_JOB_COLUMNS = frozenset({
    "status", "stage", "progress", "message", "pid",
    "records_in", "records_out", "started_at", "finished_at",
})


def telemetry_init() -> None:
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_PIPELINE_JOBS_SCHEMA)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pj_status ON pipeline_jobs (status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pj_key    ON pipeline_jobs (job_key)"
            )
            conn.execute(
                """
                UPDATE pipeline_jobs
                SET    status      = 'failed',
                       message     = 'Server restarted while job was active',
                       finished_at = datetime('now'),
                       updated_at  = datetime('now')
                WHERE  status IN ('pending', 'running')
                """
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        import logging as _log
        _log.getLogger("forge.telemetry").warning(
            f"[telemetry init] non-fatal: {exc}"
        )


def create_job(job_key: str, message: str = "") -> int:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        cur = conn.execute(
            """
            INSERT INTO pipeline_jobs
                   (job_key, status, message, started_at, updated_at)
            VALUES (?,        'pending', ?,     datetime('now'), datetime('now'))
            """,
            (job_key, message),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets: list[str] = []
    values: list[Any] = []
    for k, v in fields.items():
        if k not in _ALLOWED_JOB_COLUMNS:
            continue
        if v == "now":
            sets.append(f"{k} = datetime('now')")
        else:
            sets.append(f"{k} = ?")
            values.append(v)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    values.append(job_id)
    sql = (
        f"UPDATE pipeline_jobs SET {', '.join(sets)} "
        f"WHERE job_id = ? AND status NOT IN ('completed','failed','killed')"
    )
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        try:
            conn.execute(sql, values)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def finalize_job(job_id: int, status: str, message: str = "",
                 records_out: int = 0, progress: float = 1.0) -> None:
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        try:
            conn.execute(
                """
                UPDATE pipeline_jobs
                SET    status      = ?,
                       message     = ?,
                       records_out = ?,
                       progress    = ?,
                       finished_at = datetime('now'),
                       updated_at  = datetime('now')
                WHERE  job_id      = ?
                """,
                (status, message[:500] if message else "",
                 records_out, progress, job_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

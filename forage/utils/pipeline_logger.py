"""
FORGE — Pipeline Logger  (Phase 32)
=====================================
Shared heartbeat writer used by all collectors and engines.

Each component calls log_run() at the end of its execution to write
a row to the pipeline_runs table. The diagnostics dashboard and the
sidebar LED both read from this table.

Usage in any collector/engine:
    from forage.utils.pipeline_logger import log_run
    log_run(db_path, "usgs_collector", "success", records_in=20, records_out=5, duration_s=1.2)

The function opens its own connection so it never interferes with the
caller's transaction state.
"""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union


def log_run(
    db_path: Path,
    component: str,
    status: str,                          # 'success' | 'error'
    records_in: Optional[int] = None,     # signals fetched / pairs evaluated
    records_out: Optional[int] = None,    # signals inserted / correlations written
    duration_s: Optional[float] = None,
    detail: Optional[dict] = None,        # full summary dict → stored as JSON
) -> None:
    """
    Write one pipeline_runs row.  Silently swallows all exceptions so a
    logging failure never crashes a collector or engine.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                component   TEXT    NOT NULL,
                status      TEXT    NOT NULL
                            CHECK(status IN ('success','error')),
                records_in  INTEGER,
                records_out INTEGER,
                duration_s  REAL,
                detail_json TEXT,
                run_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO pipeline_runs
                (component, status, records_in, records_out, duration_s, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            component,
            status,
            records_in,
            records_out,
            round(duration_s, 3) if duration_s is not None else None,
            json.dumps(detail, default=str) if detail else None,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass   # logging must never crash the pipeline
#!/usr/bin/env python3
from __future__ import annotations
"""Migration: add publication columns to signals and create articles table."""

import pathlib
import sqlite3

DB_PATH = pathlib.Path(__file__).parent.parent / "database.db"


def run():
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        cur = conn.cursor()
        sig_cols = {r[1] for r in cur.execute("PRAGMA table_info(signals)")}

        if "published_at" not in sig_cols:
            cur.execute("ALTER TABLE signals ADD COLUMN published_at DATETIME DEFAULT NULL")
            print("[migration] signals.published_at added")
        else:
            print("[migration] signals.published_at already exists — skip")

        if "publish_slug" not in sig_cols:
            cur.execute("ALTER TABLE signals ADD COLUMN publish_slug TEXT DEFAULT NULL")
            print("[migration] signals.publish_slug added")
        else:
            print("[migration] signals.publish_slug already exists — skip")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                article_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT    NOT NULL,
                slug          TEXT    NOT NULL UNIQUE,
                summary       TEXT,
                body_markdown TEXT,
                stream        TEXT    NOT NULL DEFAULT 'GLOBAL'
                              CHECK(stream IN ('GLOBAL','CRIME_INTEL','INFRASTRUCTURE','PRIORITY')),
                author        TEXT    NOT NULL DEFAULT 'ZA-DIVERGENT Staff',
                status        TEXT    NOT NULL DEFAULT 'draft'
                              CHECK(status IN ('draft','published','archived')),
                published_at  DATETIME DEFAULT NULL,
                created_at    DATETIME NOT NULL DEFAULT (datetime('now')),
                updated_at    DATETIME NOT NULL DEFAULT (datetime('now')),
                tags          TEXT    DEFAULT NULL
            )
        """)
        print("[migration] articles table ready")

        conn.commit()
        print("[migration] Publication layer migration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()

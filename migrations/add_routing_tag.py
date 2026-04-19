#!/usr/bin/env python3
"""
FORGE Migration — Add routing_tag column to signals
════════════════════════════════════════════════════

Phase 63 Fix 2: Introduces a mandatory routing_tag convention so every
collector write carries its origin label, enabling auditable signal routing
and future table reconciliation.

Changes applied
───────────────
  1. ALTER TABLE signals ADD COLUMN routing_tag TEXT DEFAULT NULL
  2. Backfill: routing_tag = source for all existing rows (uses the
     existing source column as the authoritative origin label).

Idempotent: safe to re-run — skips the ALTER if the column already exists,
and UPDATE OR IGNORE prevents duplicate writes.

Usage
─────
  python migrations/add_routing_tag.py
  python migrations/add_routing_tag.py --db /path/to/database.db
  python migrations/add_routing_tag.py --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _resolve_db(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path).resolve()
    env = __import__("os").environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent / "database.db"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(db_path: Path, dry_run: bool = False) -> dict:
    print(f"[{_ts()}] [add_routing_tag] DB: {db_path}")
    if not db_path.exists():
        print(f"[{_ts()}] [add_routing_tag] ERROR: database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row

    # ── Step 1: Add column if missing ────────────────────────────────────────
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(signals)")
    }
    column_added = False

    if "routing_tag" in existing_cols:
        print(f"[{_ts()}] [add_routing_tag] Column already exists — skipping ALTER")
    else:
        if dry_run:
            print(f"[{_ts()}] [add_routing_tag] [DRY-RUN] Would ALTER TABLE signals "
                  f"ADD COLUMN routing_tag TEXT DEFAULT NULL")
        else:
            conn.execute(
                "ALTER TABLE signals ADD COLUMN routing_tag TEXT DEFAULT NULL"
            )
            conn.commit()
            column_added = True
            print(f"[{_ts()}] [add_routing_tag] Column 'routing_tag' added to signals")

    # ── Step 2: Backfill routing_tag from source column ───────────────────────
    null_count = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE routing_tag IS NULL"
    ).fetchone()[0]

    print(f"[{_ts()}] [add_routing_tag] Rows with NULL routing_tag: {null_count:,}")

    if null_count == 0:
        print(f"[{_ts()}] [add_routing_tag] Nothing to backfill")
    elif dry_run:
        print(f"[{_ts()}] [add_routing_tag] [DRY-RUN] Would backfill {null_count:,} "
              f"rows: UPDATE signals SET routing_tag = source WHERE routing_tag IS NULL")
    else:
        conn.execute(
            "UPDATE signals SET routing_tag = source WHERE routing_tag IS NULL"
        )
        conn.commit()
        updated = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE routing_tag IS NOT NULL"
        ).fetchone()[0]
        print(f"[{_ts()}] [add_routing_tag] Backfilled {null_count:,} rows "
              f"({updated:,} total now tagged)")

    # ── Step 3: Source breakdown ──────────────────────────────────────────────
    if not dry_run:
        print(f"\n[{_ts()}] [add_routing_tag] routing_tag distribution:")
        rows = conn.execute("""
            SELECT routing_tag, COUNT(*) as cnt
            FROM signals
            GROUP BY routing_tag
            ORDER BY cnt DESC
            LIMIT 20
        """).fetchall()
        for r in rows:
            tag = r[0] or "(null)"
            print(f"  {tag:<30} {r[1]:>10,}")

    conn.close()

    return {
        "status":       "ok" if not dry_run else "dry_run",
        "column_added": column_added,
        "backfilled":   null_count if not dry_run else 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE — Add routing_tag to signals")
    parser.add_argument("--db",      type=str, default=None,
                        help="Path to database.db (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be changed without writing")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    result  = run(db_path, dry_run=args.dry_run)
    sys.exit(0 if result["status"] in ("ok", "dry_run") else 1)

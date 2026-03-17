#!/usr/bin/env python3
"""
FORGE — Signal Stream Backfill  (Phase 27 utility)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Reclassifies all existing signals that have stream='GLOBAL' by running
the Phase 27 keyword classifier against their title + content.

Run once after deploying Phase 27 to populate the stream column on
signals that were ingested before the stream engine existed.

New signals ingested by rss_collector.py are classified automatically
at ingest — this script is only needed for the backfill.

Usage
─────
    python backfill_streams.py
    python backfill_streams.py --dry-run
    python backfill_streams.py --all        (reclassify ALL signals, not just GLOBAL)
    python backfill_streams.py --db C:\\path\\to\\database.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# ── Stream keyword lists (must match rss_collector.py exactly) ────────────────

CRIME_KEYWORDS = [
    "arrest", "murder", "robbery", "drug bust", "trafficking",
    "gang", "kidnapping", "smuggling", "police raid", "crime",
    "shooting", "homicide", "carjacking", "heist", "extortion",
    "organised crime", "organized crime", "syndicate", "narco",
    "interpol", "fugitive", "warrant", "conviction", "sentenced",
]

INFRASTRUCTURE_KEYWORDS = [
    "power outage", "load shedding", "loadshedding", "blackout",
    "water outage", "water supply", "pipe burst", "sewage",
    "road closure", "bridge failure", "transport disruption",
    "telecom failure", "network outage", "internet outage",
    "eskom", "infrastructure", "supply chain",
    "port congestion", "fuel shortage", "gas leak",
]

PRIORITY_ARTICLE_KEYWORDS = [
    "analysis", "investigation", "exclusive", "intelligence briefing",
    "threat assessment", "special report", "revealed", "leaked",
    "deep dive", "exposed", "classified",
]


def classify_stream(title: str, content: str) -> str:
    combined = (title + " " + (content or "")).lower()
    for kw in CRIME_KEYWORDS:
        if kw in combined:
            return "CRIME_INTEL"
    for kw in INFRASTRUCTURE_KEYWORDS:
        if kw in combined:
            return "INFRASTRUCTURE"
    for kw in PRIORITY_ARTICLE_KEYWORDS:
        if kw in combined:
            return "PRIORITY"
    return "GLOBAL"


def resolve_db(override: str | None = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    # Standard FORGE project layout
    return Path(__file__).resolve().parent / "database.db"


def run(db_path: Path, dry_run: bool = False, reclassify_all: bool = False) -> None:
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Ensure stream column exists
    existing = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if "stream" not in existing:
        print("Adding stream column to signals table...")
        conn.execute(
            "ALTER TABLE signals ADD COLUMN stream TEXT NOT NULL DEFAULT 'GLOBAL'"
        )
        conn.commit()
        print("Column added.")

    # Load signals to reclassify
    if reclassify_all:
        rows = conn.execute(
            "SELECT signal_id, title, content, stream FROM signals"
        ).fetchall()
        print(f"Reclassifying ALL {len(rows)} signals...")
    else:
        rows = conn.execute(
            "SELECT signal_id, title, content, stream FROM signals "
            "WHERE stream = 'GLOBAL' OR stream IS NULL"
        ).fetchall()
        print(f"Found {len(rows)} signals with stream=GLOBAL to reclassify...")

    if not rows:
        print("Nothing to do.")
        conn.close()
        return

    counts = {"GLOBAL": 0, "CRIME_INTEL": 0, "INFRASTRUCTURE": 0, "PRIORITY": 0}
    changes = []

    for row in rows:
        new_stream = classify_stream(row["title"] or "", row["content"] or "")
        counts[new_stream] = counts.get(new_stream, 0) + 1
        if new_stream != (row["stream"] or "GLOBAL"):
            changes.append((new_stream, row["signal_id"]))
            if dry_run:
                print(f"  [DRY] {row['signal_id'][:12]}… "
                      f"{row['stream'] or 'NULL'} → {new_stream}  "
                      f"{(row['title'] or '')[:60]}")

    print(f"\nClassification results:")
    for stream, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {stream:<20} {cnt:>5} signals")

    print(f"\n{len(changes)} signals will change stream (out of {len(rows)} evaluated)")

    if dry_run:
        print("\nDry run — no changes written.")
        conn.close()
        return

    if not changes:
        print("No changes needed — all signals already correctly classified.")
        conn.close()
        return

    # Bulk update
    conn.executemany(
        "UPDATE signals SET stream = ? WHERE signal_id = ?",
        changes
    )
    conn.commit()
    conn.close()

    print(f"\nDone — {len(changes)} signals reclassified.")
    print("Refresh /signals in your browser to see stream badges and filter pills.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Phase 27 — backfill stream classification on existing signals"
    )
    parser.add_argument("--db",      type=str, default=None,
                        help="Path to database.db (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing")
    parser.add_argument("--all",     action="store_true",
                        help="Reclassify all signals, not just GLOBAL ones")
    args = parser.parse_args()

    run(
        db_path=resolve_db(args.db),
        dry_run=args.dry_run,
        reclassify_all=args.all,
    )
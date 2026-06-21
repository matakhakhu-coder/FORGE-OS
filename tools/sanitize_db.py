#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE — Database Sanitization Utility
══════════════════════════════════════

Three-phase maintenance tool for the FORGE SQLite database:

  Phase 1  Structural Triage    — PRAGMA integrity_check + foreign_key_check
  Phase 2  Relation Cleansing   — Purge orphan rows in junction/child tables
  Phase 3  Storage Optimization — REINDEX + VACUUM to reclaim space

Safety:
  - --dry-run reports all anomalies without mutating the database
  - All writes wrapped in try/finally with conn.close() guarantee
  - Halts before Phase 2 if structural integrity fails
  - timeout=60 on all connections

Usage:
  python tools/sanitize_db.py                    # full run
  python tools/sanitize_db.py --dry-run          # report only
  python tools/sanitize_db.py --db path/to.db    # custom DB path
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("FORGE_DB", str(BASE_DIR / "database.db")))

# Junction and child tables with their FK dependencies.
# Each entry: (table, column, parent_table, parent_column)
# Only CASCADE relationships — SET NULL FKs don't produce orphans.
ORPHAN_CHECKS = [
    ("signal_actors",        "signal_id",        "signals",      "signal_id"),
    ("signal_actors",        "actor_id",         "actors",       "actor_id"),
    ("event_actors",         "event_id",         "events",       "event_id"),
    ("event_actors",         "actor_id",         "actors",       "actor_id"),
    ("actor_events",         "event_id",         "events",       "event_id"),
    ("actor_events",         "actor_id",         "actors",       "actor_id"),
    ("case_signals",         "case_id",          "cases",        "case_id"),
    ("case_signals",         "signal_id",        "signals",      "signal_id"),
    ("case_events",          "case_id",          "cases",        "case_id"),
    ("case_events",          "event_id",         "events",       "event_id"),
    ("case_artifacts",       "case_id",          "cases",        "case_id"),
    ("case_artifacts",       "artifact_id",      "artifacts",    "artifact_id"),
    ("case_actors",          "case_id",          "cases",        "case_id"),
    ("case_actors",          "actor_id",         "actors",       "actor_id"),
    ("signal_entities",      "signal_id",        "signals",      "signal_id"),
    ("signal_flags",         "signal_id",        "signals",      "signal_id"),
    ("entity_relationships", "subject_actor_id", "actors",       "actor_id"),
    ("entity_relationships", "object_actor_id",  "actors",       "actor_id"),
    ("correlated_incidents", "signal_a",         "signals",      "signal_id"),
    ("correlated_incidents", "signal_b",         "signals",      "signal_id"),
    ("actor_coalitions",     "actor_id",         "actors",       "actor_id"),
    ("actor_network_metrics","actor_id",         "actors",       "actor_id"),
    ("network_emergence",    "actor_id",         "actors",       "actor_id"),
    ("artifact_duplicates",  "artifact_id",      "artifacts",    "artifact_id"),
    ("artifact_duplicates",  "duplicate_of_id",  "artifacts",    "artifact_id"),
    ("graph_edges",          "source_node_id",   "graph_nodes",  "node_id"),
    ("graph_edges",          "target_node_id",   "graph_nodes",  "node_id"),
    ("socint_resonance",     "actor_a",          "actors",       "actor_id"),
    ("socint_resonance",     "actor_b",          "actors",       "actor_id"),
    ("enrichment_queue",     "signal_id",        "signals",      "signal_id"),
    ("wiki_links",           "source_slug",      "wiki_articles","slug"),
    ("wiki_links",           "target_slug",      "wiki_articles","slug"),
]


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def phase1_integrity(conn: sqlite3.Connection) -> bool:
    """Phase 1: Structural triage. Returns True if database is clean."""
    print(f"[{_ts()}] Phase 1 — Structural Triage")
    print(f"[{_ts()}]   Running PRAGMA integrity_check...")

    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if len(rows) == 1 and rows[0][0] == "ok":
        print(f"[{_ts()}]   integrity_check: OK")
    else:
        print(f"[{_ts()}]   INTEGRITY ERRORS DETECTED:", file=sys.stderr)
        for row in rows[:20]:
            print(f"[{_ts()}]     {row[0]}", file=sys.stderr)
        if len(rows) > 20:
            print(f"[{_ts()}]     ... and {len(rows) - 20} more", file=sys.stderr)
        return False

    print(f"[{_ts()}]   Running PRAGMA foreign_key_check...")
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if not fk_errors:
        print(f"[{_ts()}]   foreign_key_check: OK (0 violations)")
    else:
        print(f"[{_ts()}]   FK VIOLATIONS: {len(fk_errors)}", file=sys.stderr)
        seen = {}
        for row in fk_errors:
            table = row[0]
            seen[table] = seen.get(table, 0) + 1
        for table, count in sorted(seen.items(), key=lambda x: -x[1]):
            print(f"[{_ts()}]     {table}: {count} orphan rows", file=sys.stderr)
        # FK violations are fixable — don't halt, but report
        print(f"[{_ts()}]   FK violations will be cleaned in Phase 2")

    return True


def phase2_orphans(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Phase 2: Relation cleansing. Purge orphan rows."""
    print(f"[{_ts()}] Phase 2 — Relation Cleansing {'(DRY RUN)' if dry_run else ''}")

    total_orphans = 0
    total_deleted = 0
    results = {}

    # Check which tables actually exist
    existing_tables = {
        row[0] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    for child_table, child_col, parent_table, parent_col in ORPHAN_CHECKS:
        if child_table not in existing_tables or parent_table not in existing_tables:
            continue

        count_sql = (
            f"SELECT COUNT(*) FROM [{child_table}] "
            f"WHERE [{child_col}] IS NOT NULL "
            f"AND [{child_col}] NOT IN (SELECT [{parent_col}] FROM [{parent_table}])"
        )
        try:
            orphan_count = conn.execute(count_sql).fetchone()[0]
        except Exception as exc:
            print(f"[{_ts()}]   SKIP {child_table}.{child_col}: {exc}")
            continue

        if orphan_count == 0:
            continue

        total_orphans += orphan_count
        key = f"{child_table}.{child_col}"
        results[key] = orphan_count
        print(f"[{_ts()}]   {key} -> {parent_table}.{parent_col}: {orphan_count} orphans")

        if not dry_run:
            delete_sql = (
                f"DELETE FROM [{child_table}] "
                f"WHERE [{child_col}] IS NOT NULL "
                f"AND [{child_col}] NOT IN (SELECT [{parent_col}] FROM [{parent_table}])"
            )
            try:
                conn.execute(delete_sql)
                deleted = conn.execute("SELECT changes()").fetchone()[0]
                total_deleted += deleted
                print(f"[{_ts()}]     DELETED {deleted} rows")
            except Exception as exc:
                print(f"[{_ts()}]     DELETE FAILED: {exc}", file=sys.stderr)

    if total_orphans == 0:
        print(f"[{_ts()}]   No orphan records found — relations are clean")
    else:
        if dry_run:
            print(f"[{_ts()}]   Total orphans found: {total_orphans} (dry run — no deletions)")
        else:
            conn.commit()
            print(f"[{_ts()}]   Purged {total_deleted} orphan rows across {len(results)} relationships")

    return {"orphans_found": total_orphans, "deleted": total_deleted, "details": results}


def phase3_optimize(conn: sqlite3.Connection, db_path: Path, dry_run: bool) -> dict:
    """Phase 3: Storage optimization. REINDEX + VACUUM."""
    print(f"[{_ts()}] Phase 3 — Storage Optimization {'(DRY RUN)' if dry_run else ''}")

    # Get DB size before
    size_before = db_path.stat().st_size if db_path.exists() else 0
    size_before_mb = round(size_before / (1024 * 1024), 2)

    # Count indexes
    idx_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index'"
    ).fetchone()[0]
    print(f"[{_ts()}]   Indexes to rebuild: {idx_count}")

    # Page stats
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
    free_pct = round(100 * free_pages / max(page_count, 1), 1)
    print(f"[{_ts()}]   Pages: {page_count:,} total, {free_pages:,} free ({free_pct}%)")
    print(f"[{_ts()}]   DB size: {size_before_mb} MB")

    if dry_run:
        reclaimable = round(free_pages * page_size / (1024 * 1024), 2)
        print(f"[{_ts()}]   Reclaimable space: ~{reclaimable} MB")
        print(f"[{_ts()}]   DRY RUN — skipping REINDEX and VACUUM")
        return {
            "size_before_mb": size_before_mb,
            "indexes": idx_count,
            "free_pages": free_pages,
            "reclaimable_mb": reclaimable,
        }

    # REINDEX
    print(f"[{_ts()}]   Running REINDEX...")
    t0 = time.monotonic()
    conn.execute("REINDEX")
    reindex_time = round(time.monotonic() - t0, 2)
    print(f"[{_ts()}]   REINDEX complete in {reindex_time}s")

    # VACUUM must run outside any transaction and on a fresh connection
    conn.close()
    print(f"[{_ts()}]   Running VACUUM...")
    t0 = time.monotonic()
    vac_conn = sqlite3.connect(str(db_path), timeout=120)
    vac_conn.execute("VACUUM")
    vac_conn.close()
    vacuum_time = round(time.monotonic() - t0, 2)

    size_after = db_path.stat().st_size if db_path.exists() else 0
    size_after_mb = round(size_after / (1024 * 1024), 2)
    saved_mb = round(size_before_mb - size_after_mb, 2)

    print(f"[{_ts()}]   VACUUM complete in {vacuum_time}s")
    print(f"[{_ts()}]   Size: {size_before_mb} MB -> {size_after_mb} MB ({saved_mb} MB reclaimed)")

    return {
        "size_before_mb": size_before_mb,
        "size_after_mb": size_after_mb,
        "saved_mb": saved_mb,
        "indexes": idx_count,
        "reindex_time_s": reindex_time,
        "vacuum_time_s": vacuum_time,
    }


def main():
    parser = argparse.ArgumentParser(
        description="FORGE Database Sanitization Utility — integrity check, orphan purge, optimize"
    )
    parser.add_argument("--db", type=Path, default=None,
                        help="Path to database.db (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report anomalies without modifying the database")
    parser.add_argument("--skip-vacuum", action="store_true",
                        help="Skip the VACUUM step (useful for quick orphan-only runs)")
    args = parser.parse_args()

    db_path = args.db.resolve() if args.db else DB_PATH
    if not db_path.exists():
        print(f"[ERROR] Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[{_ts()}] FORGE Database Sanitizer")
    print(f"[{_ts()}] DB: {db_path}")
    print(f"[{_ts()}] Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"[{_ts()}] Size: {round(db_path.stat().st_size / (1024*1024), 2)} MB")
    print()

    start = time.monotonic()
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # Phase 1: Integrity
        integrity_ok = phase1_integrity(conn)
        print()

        if not integrity_ok:
            print(f"[{_ts()}] HALTING — structural integrity failed. Fix corruption before proceeding.",
                  file=sys.stderr)
            sys.exit(2)

        # Phase 2: Orphan purge
        orphan_result = phase2_orphans(conn, dry_run=args.dry_run)
        print()

        if args.dry_run and orphan_result["orphans_found"] > 0:
            conn.rollback()

        # Phase 3: Optimize
        if args.skip_vacuum:
            print(f"[{_ts()}] Phase 3 — Skipped (--skip-vacuum)")
            optimize_result = {"skipped": True}
        else:
            optimize_result = phase3_optimize(conn, db_path, dry_run=args.dry_run)
        print()

    finally:
        try:
            conn.close()
        except Exception:
            pass

    duration = round(time.monotonic() - start, 2)
    print(f"[{_ts()}] Complete in {duration}s")

    # Summary
    print()
    print("=" * 60)
    print("  SANITIZATION SUMMARY")
    print("=" * 60)
    print(f"  Integrity:    {'PASS' if integrity_ok else 'FAIL'}")
    print(f"  Orphans:      {orphan_result['orphans_found']} found, {orphan_result['deleted']} purged")
    if not args.skip_vacuum and not args.dry_run:
        print(f"  Space saved:  {optimize_result.get('saved_mb', 0)} MB")
        print(f"  Final size:   {optimize_result.get('size_after_mb', '?')} MB")
    elif args.dry_run and not args.skip_vacuum:
        print(f"  Reclaimable:  ~{optimize_result.get('reclaimable_mb', '?')} MB")
    print(f"  Duration:     {duration}s")
    print("=" * 60)


if __name__ == "__main__":
    main()

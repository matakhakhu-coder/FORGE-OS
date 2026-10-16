#!/usr/bin/env python3
"""
Bring a freshly initialised FORGE database up to the schema the publisher expects.

Why: app.py's first-run init builds the core tables, but the publisher also needs tables
and columns added later by other tooling (for example the `articles` table and
`signals.published_at` / `signals.publish_slug`). The old database has all of them, so
this copies only what the fresh one is missing from it.

Additive only: it creates missing tables and adds missing columns. It never drops,
renames or copies rows, and it skips full-text-search virtual tables, their shadow
tables, indexes and triggers (it lists them so you can see what was left alone).

Dry-run by default. Run from the repo root with the app stopped:
    python tools/sync_schema_from_backup.py            # show the plan
    python tools/sync_schema_from_backup.py --write    # apply it
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "database.mega-ingest-backup.db"),
                    help="database that has the full schema (read-only)")
    ap.add_argument("--dst", default=str(ROOT / "database.db"),
                    help="fresh database to bring up to date")
    ap.add_argument("--write", action="store_true", help="apply the changes")
    args = ap.parse_args()

    src_path, dst_path = pathlib.Path(args.src), pathlib.Path(args.dst)
    for p in (src_path, dst_path):
        if not p.exists():
            print(f"not found: {p}")
            return 1

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_path, timeout=60)

    src_tables = {
        name: sql
        for name, sql in src.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    dst_tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    virtual = {n for n, s in src_tables.items() if s and s.strip().upper().startswith("CREATE VIRTUAL")}

    def is_shadow(name: str) -> bool:
        return any(name.startswith(v + "_") for v in virtual)

    plan_tables: list[tuple[str, str]] = []
    plan_columns: list[tuple[str, str, str]] = []  # (table, column, ddl)
    skipped: list[str] = []

    for name, sql in src_tables.items():
        if name in virtual or is_shadow(name):
            skipped.append(f"table {name} (full-text search or its shadow table)")
            continue
        if name not in dst_tables:
            plan_tables.append((name, sql))
            continue
        have = {r[1] for r in dst.execute(f"PRAGMA table_info([{name}])")}
        for _cid, col, ctype, _notnull, dflt, pk in src.execute(f"PRAGMA table_info([{name}])"):
            if col in have:
                continue
            if pk:
                skipped.append(f"column {name}.{col} (primary key cannot be added)")
                continue
            ddl = f"ALTER TABLE [{name}] ADD COLUMN [{col}] {ctype or ''}".rstrip()
            plan_columns.append((name, col, ddl + (f" DEFAULT {dflt}" if dflt is not None else "")))

    print(f"source: {src_path.name}   target: {dst_path.name}")
    print(f"\ntables to create ({len(plan_tables)}): {[n for n, _ in plan_tables]}")
    print(f"columns to add ({len(plan_columns)}):")
    for table, col, _ in plan_columns:
        print(f"  {table}.{col}")
    if skipped:
        print(f"\nleft alone ({len(skipped)}):")
        for s in skipped:
            print(f"  {s}")

    if not args.write:
        print("\nDRY RUN: nothing changed. Re-run with --write to apply.")
        return 0

    failed: list[str] = []
    with dst:
        for name, sql in plan_tables:
            try:
                dst.execute(sql)
            except sqlite3.Error as exc:
                failed.append(f"create {name}: {exc}")
        for table, col, ddl in plan_columns:
            try:
                dst.execute(ddl)
            except sqlite3.OperationalError:
                # a non-constant default (such as CURRENT_TIMESTAMP) cannot be added to an
                # existing table; retry without the default
                try:
                    dst.execute(ddl.split(" DEFAULT ")[0])
                except sqlite3.Error as exc:
                    failed.append(f"add {table}.{col}: {exc}")
    dst.close()

    print("\nAPPLIED.")
    if failed:
        print("These could not be applied:")
        for f in failed:
            print(f"  {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
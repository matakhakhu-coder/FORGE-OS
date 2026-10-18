#!/usr/bin/env python3
"""
Shortcut to tools/checked_signals.py, kept so old notes and commands still work.

The two per-file seed commands are no longer needed: one command loads every
batch listed in docs/signals/index.json, in order.

    python tools/checked_signals.py import            # dry run
    python tools/checked_signals.py import --write    # apply

This script does the same. A file argument is still accepted for old
commands, but it only has to be listed in the index: all batches are loaded.

    python tools/seed_checked_signals.py [docs/signals/<batch>.json] [--write] [--db PATH]
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools import checked_signals as cs_mod  # noqa: E402
from tools.checked_signals import (  # noqa: E402,F401  (re-exported)
    Batch, CheckedSet, CheckedSignalError, DB_PATH, INDEX_PATH, load_index,
)


def load_batch(path: pathlib.Path) -> list[dict]:
    """Validate one batch file strictly and return its pins."""
    return cs_mod.load_batch_file(pathlib.Path(path)).signals


def seed(conn: sqlite3.Connection, signals: list[dict], write: bool) -> list[str]:
    """Import already-validated pins (one batch, no retractions)."""
    cs_mod._reject_duplicates(signals)
    cset = CheckedSet([Batch(path=pathlib.Path("<seed>"), province=None, signals=signals)])
    return [a.line() for a in cs_mod.import_checked(conn, cset, write=write)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", type=pathlib.Path, nargs="?", help="optional; must be listed in the index")
    ap.add_argument("--write", action="store_true", help="apply (default is a dry run)")
    ap.add_argument("--db", type=pathlib.Path, default=DB_PATH)
    ap.add_argument("--index", type=pathlib.Path, default=INDEX_PATH)
    args = ap.parse_args()
    try:
        if args.batch is not None:
            listed = {b.path.resolve() for b in load_index(args.index).batches}
            if args.batch.resolve() not in listed:
                raise CheckedSignalError(f"{args.batch} is not listed in {args.index}")
            print(f"Note: loading every batch in {args.index.name}, not just {args.batch.name}.")
        cs_mod.run_import(args.db, args.index, args.write)
    except CheckedSignalError as exc:
        print(f"[checked] refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

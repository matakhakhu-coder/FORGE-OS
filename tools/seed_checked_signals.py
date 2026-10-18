#!/usr/bin/env python3
"""
Load hand-checked, standalone signals from a JSON file into database.db.

Why: the live map is rebuilt from database.db by tools/publish.py. Signals that
were checked by hand on another machine have to be in your database too, or
the next rebuild drops them from the map. This puts them back exactly as
checked: published, status 'reviewed', no gravity score, and processed_at set
so tools/mega_ingest.py never re-scores them or links them to people or cases.

Dry-run by default (prints what it would do). Pass --write to change the database.
Safe to re-run: it updates the same signals instead of duplicating them.

Run from the repo root:
    python tools/seed_checked_signals.py docs/signals/western-cape-murders-2026-10-18.json
    python tools/seed_checked_signals.py docs/signals/western-cape-murders-2026-10-18.json --write
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database.db"

REQUIRED = ("signal_id", "external_id", "source", "title", "content", "lat", "lng",
            "timestamp", "published_at", "metadata")
STREAMS = {"GLOBAL", "CRIME_INTEL", "INFRASTRUCTURE", "PRIORITY"}


def load_batch(path: pathlib.Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    signals = data["signals"] if isinstance(data, dict) else data
    for s in signals:
        missing = [k for k in REQUIRED if s.get(k) in (None, "")]
        if missing:
            raise ValueError(f"{s.get('signal_id')}: missing {missing}")
        if not isinstance(s["metadata"].get("checked"), dict):
            raise ValueError(f"{s['signal_id']}: metadata.checked is required")
        if s.get("stream", "CRIME_INTEL") not in STREAMS:
            raise ValueError(f"{s['signal_id']}: unknown stream {s.get('stream')}")
        if not (-90 <= float(s["lat"]) <= 90 and -180 <= float(s["lng"]) <= 180):
            raise ValueError(f"{s['signal_id']}: lat/lng out of range")
    return signals


def seed(conn: sqlite3.Connection, signals: list[dict], write: bool) -> list[str]:
    actions = []
    for s in signals:
        exists = conn.execute("SELECT 1 FROM signals WHERE signal_id = ?", (s["signal_id"],)).fetchone()
        actions.append(f"{'update' if exists else 'insert'} {s['signal_id']}  {s['title']}")
        if not write:
            continue
        conn.execute(
            """INSERT INTO signals
                   (signal_id, source, external_id, title, content, lat, lng, timestamp,
                    status, metadata_json, is_priority, stream, relevance_score, source_type,
                    gravity_score, processed_at, published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reviewed', ?, 0, ?, 1.0, 'news', NULL, ?, ?)
               ON CONFLICT(signal_id) DO UPDATE SET
                   source=excluded.source, title=excluded.title, content=excluded.content,
                   lat=excluded.lat, lng=excluded.lng, status='reviewed',
                   metadata_json=excluded.metadata_json, stream=excluded.stream,
                   gravity_score=NULL, processed_at=excluded.processed_at,
                   published_at=excluded.published_at""",
            (s["signal_id"], s["source"], s["external_id"], s["title"], s["content"],
             float(s["lat"]), float(s["lng"]), s["timestamp"],
             json.dumps(s["metadata"], ensure_ascii=False), s.get("stream", "CRIME_INTEL"),
             s["published_at"], s["published_at"]),
        )
    if write:
        conn.commit()
    return actions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", type=pathlib.Path, help="JSON file of checked signals")
    ap.add_argument("--write", action="store_true", help="apply (default is a dry run)")
    ap.add_argument("--db", type=pathlib.Path, default=DB_PATH)
    args = ap.parse_args()
    signals = load_batch(args.batch)
    conn = sqlite3.connect(str(args.db))
    for line in seed(conn, signals, args.write):
        print(("" if args.write else "[dry run] ") + line)
    if not args.write:
        print("Nothing written. Re-run with --write to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
from __future__ import annotations
"""
Ingest actor photos dropped in media/actor_photos_inbox/ into media/actors/
and register them on the actors.image_url column.

File naming convention: <actor_id>.<ext>  (e.g. 17.jpg, 59.png)
Allowed extensions: png, jpg, jpeg, webp, gif

Usage:
    python tools/ingest_actor_photos.py
"""

import pathlib
import shutil
import sqlite3

ROOT       = pathlib.Path(__file__).parent.parent
DB_PATH    = ROOT / "database.db"
INBOX      = ROOT / "media" / "actor_photos_inbox"
ACTORS_DIR = ROOT / "media" / "actors"
ALLOWED    = {"png", "jpg", "jpeg", "webp", "gif"}


def main() -> None:
    ACTORS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    processed = []
    for f in sorted(INBOX.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower().lstrip(".")
        if ext not in ALLOWED:
            print(f"[skip] {f.name} -- unsupported extension")
            continue
        stem = f.stem
        if not stem.isdigit():
            print(f"[skip] {f.name} -- filename must be <actor_id>.<ext>")
            continue
        actor_id = int(stem)
        row = conn.execute("SELECT name FROM actors WHERE actor_id = ?", (actor_id,)).fetchone()
        if not row:
            print(f"[skip] {f.name} -- no actor with id {actor_id}")
            continue

        dest_name = f"{actor_id}.{ext}"
        dest = ACTORS_DIR / dest_name

        # remove any prior photo with a different extension for this actor
        for old in ACTORS_DIR.glob(f"{actor_id}.*"):
            if old.name != dest_name:
                old.unlink()

        shutil.copy2(f, dest)
        conn.execute("UPDATE actors SET image_url = ? WHERE actor_id = ?", (f"actors/{dest_name}", actor_id))
        f.unlink()
        processed.append((actor_id, row["name"], dest_name))
        print(f"[ok]   {row['name']} (#{actor_id}) -> {dest_name}")

    conn.commit()
    conn.close()
    print(f"\n{len(processed)} photo(s) registered.")


if __name__ == "__main__":
    main()

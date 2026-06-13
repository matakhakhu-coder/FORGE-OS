#!/usr/bin/env python3
from __future__ import annotations
"""
One-off / repeatable: for directory actors with zero signal_actors links,
text-match their name (or surname, for multi-word names) against the
title/content of signals pinned to their linked published cases, and
insert signal_actors rows where there's textual evidence.

This keeps actor activity-stat infoboxes (First/Last Observed, Linked
Signals, Primary Stream) canon — only links an actor to a signal that
actually names them, rather than bulk-linking every case signal.

Usage:
    python tools/link_actor_case_signals.py
"""

import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "database.db"

PUBLISHED_CASE_IDS = (7, 8, 9, 10, 11, 12, 13)


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        ph = ",".join("?" * len(PUBLISHED_CASE_IDS))

        actors = conn.execute(f"""
            SELECT DISTINCT a.actor_id, a.name
            FROM actors a
            WHERE (a.actor_id IN (SELECT actor_id FROM case_actors WHERE case_id IN ({ph}))
               OR (a.type='person' AND a.confidence_score>=0.35))
              AND a.name NOT IN ('location','government','company','sa','south africa',
                                  'gauteng','pretoria','johannesburg','cape town','kzn',
                                  'kwazulu-natal')
              AND a.type NOT IN ('location')
        """, PUBLISHED_CASE_IDS).fetchall()

        for a in actors:
            aid, name = a["actor_id"], a["name"]
            existing = conn.execute(
                "SELECT COUNT(*) FROM signal_actors WHERE actor_id=?", (aid,)
            ).fetchone()[0]
            if existing > 0:
                continue

            cases = [r["case_id"] for r in conn.execute(
                f"SELECT case_id FROM case_actors WHERE actor_id=? AND case_id IN ({ph})",
                (aid, *PUBLISHED_CASE_IDS)
            ).fetchall()]
            if not cases:
                continue

            cph = ",".join("?" * len(cases))
            sigs = conn.execute(f"""
                SELECT DISTINCT s.signal_id, s.title, s.content
                FROM case_signals cs JOIN signals s ON s.signal_id = cs.signal_id
                WHERE cs.case_id IN ({cph})
            """, cases).fetchall()

            name_lower = name.lower()
            parts = name.split()
            surname = parts[-1].lower() if len(parts) > 1 else None

            linked = 0
            for s in sigs:
                text = f"{s['title']} {s['content'] or ''}".lower()
                if name_lower in text or (surname and surname in text):
                    conn.execute(
                        "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) "
                        "VALUES (?, ?, 'mentioned')",
                        (s["signal_id"], aid),
                    )
                    linked += 1

            if linked:
                print(f"{aid:>3} {name:<45} -> linked {linked}/{len(sigs)} case signals")
            else:
                print(f"{aid:>3} {name:<45} -> no text match in {len(sigs)} case signals")

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()

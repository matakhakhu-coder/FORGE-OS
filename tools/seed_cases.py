"""
FORGE — seed_cases.py
=====================
Populates the archive with test Case Workspaces (Phase 8/9).
Designed to work alongside the existing `seed_data.py`.

Run this script AFTER running `seed_data.py`:

    python seed_cases.py
"""

import sqlite3
import argparse
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"

# ---------------------------------------------------------------------------
# Seed Data: Cases & Pins
# ---------------------------------------------------------------------------

CASES = [
    (
        "Operation Sandstorm Leak Investigation",
        "Investigating the source and distribution network of the August 2019 "
        "SSA directive leak. Focus is on identifying the internal leaker and "
        "their connection to The Soweto Collective.",
        "active"
    ),
    (
        "Electoral Irregularities (Gauteng)",
        "Tracking the fallout from Commissioner Dlamini's memo regarding the "
        "May 2019 general election discrepancies.",
        "closed"
    )
]

# (case_index, entity_type, entity_name_or_title, note)
# We use names/titles to find the IDs dynamically, ensuring it works regardless
# of how the auto-increment IDs were assigned in `seed_data.py`.
PINS = [
    # ── Case 0: Operation Sandstorm Leak ──
    (0, "actor", "State Security Agency (SSA)", "Origin point of the leaked directive."),
    (0, "actor", "The Soweto Collective", "Distribution point for the leak. Who is their source?"),
    (0, "event", "Operation Sandstorm — SSA Internal Fracture", "The core event surrounding the directive."),
    (0, "artifact", "SSA Directive — Operation Sandstorm (Partial)", "The primary physical evidence."),
    
    # ── Case 1: Electoral Irregularities ──
    (1, "actor", "Commissioner Nozipho Dlamini", "Author of the founding document."),
    (1, "actor", "Electoral Commission of South Africa (IEC)", "Institution under investigation."),
    (1, "event", "The Disputed Count — IEC Gauteng Recount Order", "The triggering event."),
    (1, "artifact", "The Dlamini Document", "Primary forensic evidence."),
    (1, "artifact", "IEC Press Conference — Recount Announcement", "Public confirmation of the recount.")
]

# ---------------------------------------------------------------------------
# Seeding Logic
# ---------------------------------------------------------------------------

def seed_cases(conn: sqlite3.Connection):
    cur = conn.cursor()

    # 1. Insert Cases
    case_ids = []
    print("[seed_cases] Creating Case Workspaces...")
    for title, description, status in CASES:
        cur.execute(
            "INSERT INTO cases (title, description, status, source_type) VALUES (?, ?, ?, 'seed')",
            (title, description, status)
        )
        case_ids.append(cur.lastrowid)

    # Helper function to find entity IDs by name/title
    def get_entity_id(entity_type, name):
        if entity_type == "actor":
            row = cur.execute("SELECT actor_id FROM actors WHERE name = ?", (name,)).fetchone()
            return row[0] if row else None
        elif entity_type == "event":
            row = cur.execute("SELECT event_id FROM events WHERE title = ?", (name,)).fetchone()
            return row[0] if row else None
        elif entity_type == "artifact":
            row = cur.execute("SELECT artifact_id FROM artifacts WHERE title = ?", (name,)).fetchone()
            return row[0] if row else None
        return None

    # 2. Insert Pins
    print("[seed_cases] Pinning Entities to Cases...")
    for case_index, entity_type, entity_name, note in PINS:
        case_id = case_ids[case_index]
        entity_id = get_entity_id(entity_type, entity_name)

        if not entity_id:
            print(f"  [Warning] Could not find {entity_type}: '{entity_name}'. Skipping pin.")
            continue

        table_map = {
            "actor": ("case_actors", "actor_id"),
            "event": ("case_events", "event_id"),
            "artifact": ("case_artifacts", "artifact_id")
        }
        
        table, id_col = table_map[entity_type]
        
        try:
            cur.execute(
                f"INSERT INTO {table} (case_id, {id_col}, note) VALUES (?, ?, ?)",
                (case_id, entity_id, note)
            )
        except sqlite3.IntegrityError:
            print(f"  [Warning] Pin already exists for {entity_type} '{entity_name}' in Case {case_id}")

    conn.commit()

def reset_cases(conn: sqlite3.Connection):
    """Wipe only the case-related tables."""
    print("[seed_cases] Wiping existing Case data...")
    for table in ["case_artifacts", "case_events", "case_actors", "cases"]:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()

def main():
    if not DB_PATH.exists():
        print(f"[seed_cases] ERROR: Database not found at {DB_PATH}")
        return

    parser = argparse.ArgumentParser(description="FORGE case seed loader")
    parser.add_argument("--reset", action="store_true", help="Wipe existing cases before seeding.")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")

    if args.reset:
        reset_cases(conn)

    seed_cases(conn)
    
    case_count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    print(f"[seed_cases] Complete. Total Cases in DB: {case_count}")
    
    conn.close()

if __name__ == "__main__":
    main()
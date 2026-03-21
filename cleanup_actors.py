"""
FORGE — Data Cleanup Script
Renames legacy v1.0 actor names to canonical v2.0 names.
Fixes event titles to use actual signal content.
Safe to run multiple times — all operations are idempotent.
"""
import sqlite3

conn = sqlite3.connect("database.db")
cur  = conn.cursor()

# ── 1. Rename legacy actor names ──────────────────────────────────────────────
renames = [
    ("location_ZA",    "South Africa"),
    ("Municipalities", "South African Municipalities"),
    ("SAPS",           "South African Police Service"),
    ("Hawks",          "Directorate for Priority Crime Investigation"),
    ("ANC",            "African National Congress"),
    ("DA",             "Democratic Alliance"),
    ("SARB",           "South African Reserve Bank"),
    ("Ramaphosa",      "Cyril Ramaphosa"),
    ("NPA",            "National Prosecuting Authority"),
]

for old, new in renames:
    # Check if canonical name already exists (from v2.0 run)
    existing = cur.execute(
        "SELECT actor_id FROM actors WHERE name = ?", (new,)
    ).fetchone()
    legacy = cur.execute(
        "SELECT actor_id FROM actors WHERE name = ?", (old,)
    ).fetchone()

    if existing and legacy:
        # Merge: move signal_actors and event_actors from legacy to canonical
        cur.execute(
            "UPDATE OR IGNORE signal_actors SET actor_id=? WHERE actor_id=?",
            (existing[0], legacy[0])
        )
        cur.execute(
            "UPDATE OR IGNORE event_actors SET actor_id=? WHERE actor_id=?",
            (existing[0], legacy[0])
        )
        cur.execute("DELETE FROM actors WHERE actor_id=?", (legacy[0],))
        print(f"  Merged '{old}' into existing '{new}'")
    elif legacy:
        cur.execute("UPDATE actors SET name=? WHERE name=?", (new, old))
        print(f"  Renamed '{old}' → '{new}'")
    else:
        print(f"  Skipped '{old}' (not found)")

# ── 2. Fix event titles from signal content ───────────────────────────────────
cur.execute("""
    UPDATE events
    SET title = (
        SELECT SUBSTR(s.title, 1, 80)
        FROM   signals s
        WHERE  s.signal_id = REPLACE(
                   events.description,
                   'Auto-generated from signal ', ''
               )
        AND    s.title IS NOT NULL
        AND    s.title != ''
    )
    WHERE  title LIKE 'Detected: %'
    AND    description LIKE 'Auto-generated from signal %'
""")
print(f"  Fixed {cur.rowcount} event titles from signal content")

# ── 3. Ensure all automated actors are source_type=live ──────────────────────
cur.execute("UPDATE actors SET source_type='live' WHERE automated=1")
print(f"  Set {cur.rowcount} automated actors to source_type=live")

# ── 4. Set all automated actors to source_type=live ──────────────────────────
cur.execute("UPDATE actors SET source_type='live' WHERE automated=1")
print(f"  Ensured {cur.rowcount} automated actors visible in LIVE lens")

# ── 5. Fix location actors typed as institution ───────────────────────────────
location_names = [
    "Gauteng", "Western Cape", "Eastern Cape", "KwaZulu-Natal",
    "Limpopo", "Mpumalanga", "North West", "Free State", "Northern Cape",
    "South Africa", "Cape Town", "Johannesburg", "Pretoria", "Durban",
    "Soweto", "Sandton", "Ekurhuleni", "East London", "Pietermaritzburg",
    "Bloemfontein", "Mbombela", "Polokwane", "Mahikeng", "Kimberley",
    "Newcastle", "Port Elizabeth",
]
for name in location_names:
    cur.execute(
        "UPDATE actors SET type='institution' WHERE name=? AND automated=1",
        (name,)
    )
print(f"  Location actors normalised")

conn.commit()
conn.close()
print("\nDone.")
"""
FORGE — seed_data.py
====================
Populates the archive with ZA-Divergent alternate history test data.

Scenario
--------
South Africa, 2019–2021.  In this divergent timeline, a disputed election
result triggers a constitutional crisis.  A parallel intelligence apparatus
emerges, the SSA fractures, and a civil unrest cascade begins in Gauteng
before spreading nationally.

Run this script AFTER initialising the database:

    python app.py --init-db
    python seed_data.py

To wipe and re-seed:

    python seed_data.py --reset

The script is fully idempotent when run with --reset: it clears all data,
reseeds, and rebuilds the FTS5 indices from the content tables.
"""

import sys
import sqlite3
import argparse
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "database.db"


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

# ── Actors ──────────────────────────────────────────────────────────────────
#
# (name, type, description)

ACTORS = [
    (
        "State Security Agency (SSA)",
        "government",
        "South Africa's primary domestic intelligence body. In the divergent "
        "timeline, the SSA fractures into two rival directorates following "
        "the disputed 2019 election — the Loyalist Directorate, aligned with "
        "the incumbent administration, and the Reform Directorate, backing "
        "the electoral commission's audit findings.",
    ),
    (
        "Electoral Commission of South Africa (IEC)",
        "institution",
        "The independent body responsible for managing national elections. "
        "In the divergent timeline, the IEC's announcement of a recount "
        "triggers the constitutional crisis. Its commissioners face personal "
        "security threats from mid-2019 onwards.",
    ),
    (
        "Gauteng People's Assembly (GPA)",
        "movement",
        "A decentralised civic movement that emerged from township community "
        "structures in Soweto and Alexandra. The GPA organised the largest "
        "post-apartheid civil demonstrations, peaking at an estimated "
        "400,000 participants in the October 2020 Pretoria March.",
    ),
    (
        "South African Police Service (SAPS)",
        "government",
        "National police service. In the divergent timeline, SAPS command "
        "is split: urban units largely follow civilian government orders "
        "while rural provincial commands align with the military interim "
        "council. This split becomes visible during the Marikana Corridor "
        "Incident of March 2021.",
    ),
    (
        "Commissioner Nozipho Dlamini",
        "person",
        "Senior IEC Commissioner responsible for the Gauteng provincial "
        "count in the 2019 election. Her leaked memo — 'the Dlamini "
        "Document' — confirming systematic ballot irregularities in three "
        "Gauteng constituencies becomes the founding artifact of the "
        "constitutional crisis.",
    ),
    (
        "General Sipho Ndlovu",
        "person",
        "Commanding General of the South African National Defence Force "
        "(SANDF) Joint Operations Division. His ambiguous public statement "
        "on 14 January 2020 — neither endorsing nor condemning the interim "
        "council — is widely interpreted as a tacit military endorsement "
        "of the constitutional freeze.",
    ),
    (
        "The Soweto Collective",
        "media",
        "An independent community journalism network operating out of "
        "Meadowlands, Soweto. The Collective's encrypted Telegram channel "
        "becomes a primary distribution point for leaked government "
        "documents and citizen footage during the crisis period.",
    ),
]

# ── Events ───────────────────────────────────────────────────────────────────
#
# (title, summary, date, location, latitude, longitude, category)

EVENTS = [
    (
        "The Disputed Count — IEC Gauteng Recount Order",
        "Following the May 2019 general election, the IEC orders a full "
        "recount of ballots in three Gauteng constituencies after statistical "
        "anomalies are identified in the electronic tabulation system. The "
        "ruling party contests the order, triggering the first phase of the "
        "constitutional crisis. Commissioner Dlamini's internal memo is "
        "leaked to The Soweto Collective within 72 hours.",
        "2019-06-04",
        "Electoral Court, Johannesburg",
        -26.2041,
        28.0473,
        "Election",
    ),
    (
        "Operation Sandstorm — SSA Internal Fracture",
        "Classified SSA operational directive 'Sandstorm' is activated, "
        "authorising the Loyalist Directorate to surveil IEC commissioners "
        "and senior civil servants identified as sympathetic to the recount "
        "process. The directive is leaked six months later by an anonymous "
        "source within the Reform Directorate, becoming the archive's most "
        "significant document leak.",
        "2019-08-17",
        "SSA Headquarters, Pretoria",
        -25.7461,
        28.1881,
        "Security",
    ),
    (
        "The Constitutional Freeze — Parliament Suspended",
        "After months of legal deadlock, the incumbent administration "
        "invokes emergency powers under Section 37 of the Constitution, "
        "suspending parliamentary sessions indefinitely pending a "
        "Constitutional Court ruling. Civil society organisations immediately "
        "challenge the suspension. The Gauteng People's Assembly is formally "
        "established at a mass meeting in Soweto the same evening.",
        "2020-01-12",
        "National Assembly, Cape Town",
        -33.9249,
        18.4241,
        "Legislative",
    ),
    (
        "The Pretoria March — GPA Mass Demonstration",
        "The Gauteng People's Assembly leads an estimated 400,000 people "
        "in a march from Johannesburg's Mary Fitzgerald Square to the Union "
        "Buildings in Pretoria — the largest civil demonstration in South "
        "Africa since the anti-apartheid era. SAPS urban units stand down. "
        "General Ndlovu issues his ambiguous statement. Seventeen hours of "
        "citizen footage are uploaded to The Soweto Collective's channel "
        "within 24 hours.",
        "2020-10-21",
        "Union Buildings, Pretoria",
        -25.7069,
        28.2294,
        "Civil Unrest",
    ),
    (
        "Marikana Corridor Incident",
        "SAPS rural units from North West Province attempt to establish a "
        "checkpoint cordon along the N4 corridor near Marikana, citing "
        "emergency regulations. Urban SAPS units from Tshwane refuse to "
        "participate. The stand-off, lasting 11 hours, is broadcast live "
        "by a GPA-affiliated journalist embedded with the rural unit. It "
        "becomes the clearest evidence of the SAPS internal fracture.",
        "2021-03-08",
        "N4 Corridor, Marikana",
        -25.7006,
        27.4806,
        "Security",
    ),
]

# ── Artifacts ─────────────────────────────────────────────────────────────────
#
# (title, description, type, date, location, latitude, longitude,
#  tags, source, event_index)
#
# event_index is a 0-based index into EVENTS above.

ARTIFACTS = [
    (
        "The Dlamini Document",
        "A four-page internal IEC memorandum authored by Commissioner "
        "Nozipho Dlamini. The document presents statistical evidence of "
        "systematic irregularities in the electronic ballot tabulation "
        "system across three Gauteng constituencies. Leaked to The Soweto "
        "Collective on 7 June 2019 via encrypted transfer. The document's "
        "authenticity has been independently verified by two forensic "
        "document analysts.",
        "document",
        "2019-06-04",
        "Electoral Court, Johannesburg",
        -26.2041,
        28.0473,
        "IEC,election,ballot,irregularity,Gauteng,Dlamini,leaked",
        "leaked",
        0,  # → The Disputed Count
    ),
    (
        "IEC Press Conference — Recount Announcement",
        "News broadcast recording of the IEC's official press conference "
        "announcing the Gauteng recount order. Commissioner Dlamini reads "
        "a prepared statement. The recording captures the moment a ruling "
        "party spokesperson interrupts to contest the announcement. Sourced "
        "from SABC archive footage.",
        "video",
        "2019-06-05",
        "IEC National Results Centre, Johannesburg",
        -26.2041,
        28.0473,
        "IEC,election,recount,press conference,Dlamini,SABC",
        "media",
        0,  # → The Disputed Count
    ),
    (
        "SSA Directive — Operation Sandstorm (Partial)",
        "A partially redacted scan of SSA operational directive 7/2019, "
        "codenamed 'Sandstorm'. Sections 4 through 9 are visible, "
        "detailing surveillance authorisations targeting twelve named "
        "civil servants. The source of this leak is unknown; the document "
        "was transmitted to the archive via anonymous encrypted drop.",
        "document",
        "2019-08-17",
        "SSA Headquarters, Pretoria",
        -25.7461,
        28.1881,
        "SSA,intelligence,surveillance,Sandstorm,directive,leaked",
        "leaked",
        1,  # → Operation Sandstorm
    ),
    (
        "Section 37 Emergency Proclamation — Official Gazette",
        "Scanned copy of the Government Gazette (Vol. 661, No. 42891) "
        "containing the executive proclamation invoking Section 37 "
        "emergency powers and suspending parliamentary sessions. Official "
        "government source. Date-stamped 12 January 2020.",
        "document",
        "2020-01-12",
        "Government Printer, Pretoria",
        -25.7461,
        28.1881,
        "Section 37,emergency,parliament,suspension,gazette,government",
        "government",
        2,  # → The Constitutional Freeze
    ),
    (
        "GPA Formation Meeting — Soweto, Eyewitness Audio",
        "Audio recording of the founding mass meeting of the Gauteng "
        "People's Assembly at Orlando Stadium, Soweto, on the evening of "
        "12 January 2020. Recording captures the adoption of the GPA "
        "charter and the appointment of the first steering committee. "
        "Approximately 23,000 people in attendance. Recorded by a "
        "community journalist affiliated with The Soweto Collective.",
        "audio",
        "2020-01-12",
        "Orlando Stadium, Soweto",
        -26.2309,
        27.9320,
        "GPA,Soweto,formation,assembly,civil society,charter",
        "citizen",
        2,  # → The Constitutional Freeze
    ),
    (
        "Pretoria March — Aerial Footage, Hour 3",
        "Citizen drone footage capturing the march column on Madiba Street "
        "approaching Church Square, Pretoria, approximately three hours "
        "into the demonstration. The footage shows the scale of the crowd "
        "and the absence of SAPS presence along the central route. "
        "Uploaded to The Soweto Collective's Telegram channel on "
        "21 October 2020.",
        "video",
        "2020-10-21",
        "Madiba Street, Pretoria CBD",
        -25.7461,
        28.1881,
        "GPA,march,Pretoria,protest,drone,crowd,SAPS,October 2020",
        "citizen",
        3,  # → The Pretoria March
    ),
    (
        "General Ndlovu Statement — SABC Radio Transcript",
        "Verbatim transcript of General Sipho Ndlovu's 97-second radio "
        "statement broadcast on SABC Radio Metro on 21 October 2020, "
        "timed to coincide with the Pretoria March. The statement neither "
        "endorses nor condemns the interim council. Analysts note the "
        "deliberate omission of the phrase 'lawful government', standard "
        "in SANDF public communications.",
        "document",
        "2020-10-21",
        "SABC Radio Metro Studios, Auckland Park",
        -26.1751,
        28.0123,
        "SANDF,Ndlovu,military,statement,SABC,constitutional crisis",
        "media",
        3,  # → The Pretoria March
    ),
    (
        "Marikana Corridor — GPA Journalist Embedded Footage",
        "Eleven-hour live broadcast recording from a GPA-affiliated "
        "journalist embedded with the SAPS rural unit during the N4 "
        "corridor stand-off. The footage captures the checkpoint "
        "establishment, the refusal of Tshwane urban units to participate, "
        "and the eventual stand-down. Key evidence of the SAPS fracture. "
        "Unedited; source identity protected.",
        "video",
        "2021-03-08",
        "N4 Corridor, Marikana",
        -25.7006,
        27.4806,
        "SAPS,Marikana,checkpoint,fracture,N4,stand-off,GPA,citizen footage",
        "citizen",
        4,  # → Marikana Corridor Incident
    ),
    (
        "Marikana Checkpoint — Citizen Photographs",
        "A set of 34 photographs taken by a local resident from a "
        "farmhouse overlooking the N4 checkpoint. The photographs document "
        "the physical layout of the cordon, the vehicles involved, and "
        "the moment of stand-down. Submitted anonymously to the archive "
        "via The Soweto Collective.",
        "photo",
        "2021-03-08",
        "N4 Corridor, Marikana",
        -25.7006,
        27.4806,
        "SAPS,Marikana,checkpoint,photographs,evidence,N4",
        "citizen",
        4,  # → Marikana Corridor Incident
    ),
]

# ── Actor → Event links ────────────────────────────────────────────────────────
#
# (actor_index, event_index, role)
# Indices are 0-based into ACTORS and EVENTS above.

ACTOR_EVENTS = [
    (1, 0, "Presiding Body"),           # IEC → Disputed Count
    (4, 0, "Lead Commissioner"),        # Dlamini → Disputed Count
    (6, 0, "Leak Recipient"),           # Soweto Collective → Disputed Count

    (0, 1, "Issuing Authority"),        # SSA → Operation Sandstorm
    (4, 1, "Surveillance Target"),      # Dlamini → Operation Sandstorm
    (1, 1, "Subject of Surveillance"),  # IEC → Operation Sandstorm

    (1, 2, "Suspended Institution"),    # IEC → Constitutional Freeze
    (2, 2, "Founding Movement"),        # GPA → Constitutional Freeze

    (2, 3, "Organising Movement"),      # GPA → Pretoria March
    (3, 3, "Stand-Down Force"),         # SAPS → Pretoria March
    (5, 3, "Ambiguous Observer"),       # Ndlovu → Pretoria March
    (6, 3, "Media Distribution"),       # Soweto Collective → Pretoria March

    (3, 4, "Fractured Force"),          # SAPS → Marikana Incident
    (2, 4, "Embedded Journalist Source"), # GPA → Marikana Incident
    (6, 4, "Archive Recipient"),        # Soweto Collective → Marikana Incident
]


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------

def seed(conn: sqlite3.Connection):
    cur = conn.cursor()

    # Insert actors — capture generated IDs
    actor_ids = []
    for name, atype, description in ACTORS:
        cur.execute(
            "INSERT INTO actors (name, type, description) VALUES (?, ?, ?)",
            (name, atype, description),
        )
        actor_ids.append(cur.lastrowid)

    # Insert events — capture generated IDs
    event_ids = []
    for title, summary, date, location, lat, lon, category in EVENTS:
        cur.execute(
            """INSERT INTO events
               (title, summary, date, location, latitude, longitude, category)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (title, summary, date, location, lat, lon, category),
        )
        event_ids.append(cur.lastrowid)

    # Insert artifacts — resolve event_index to real event_id
    for (title, description, atype, date, location, lat, lon,
         tags, source, event_index) in ARTIFACTS:
        cur.execute(
            """INSERT INTO artifacts
               (title, description, type, date, location,
                latitude, longitude, tags, source, event_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, atype, date, location,
             lat, lon, tags, source, event_ids[event_index]),
        )

    # Insert actor_events — resolve both indices to real IDs
    for actor_index, event_index, role in ACTOR_EVENTS:
        cur.execute(
            "INSERT INTO actor_events (actor_id, event_id, role) VALUES (?, ?, ?)",
            (actor_ids[actor_index], event_ids[event_index], role),
        )

    conn.commit()


def rebuild_fts(conn: sqlite3.Connection):
    """
    Rebuild both FTS5 indices from their content tables.
    This is faster and safer than relying on triggers for bulk inserts,
    and ensures the search index is fully consistent after seeding.
    """
    conn.execute("INSERT INTO artifacts_fts(artifacts_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")
    conn.commit()


def reset_data(conn: sqlite3.Connection):
    """Delete all rows from data tables (preserves schema)."""
    for table in ("actor_events", "artifacts", "events", "actors"):
        conn.execute(f"DELETE FROM {table}")
    # Clear FTS indices
    conn.execute("INSERT INTO artifacts_fts(artifacts_fts) VALUES('delete-all')")
    conn.execute("INSERT INTO events_fts(events_fts) VALUES('delete-all')")
    conn.commit()


def verify(conn: sqlite3.Connection):
    """Print a summary of inserted records and run test FTS5 queries."""
    print("\n[FORGE] ── Verification ──────────────────────────────────────")

    for table in ("actors", "events", "artifacts", "actor_events"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<20} {count:>3} rows")

    # Spot-check: relationship chain for the Pretoria March
    print("\n  Relationship chain: Pretoria March → Actors")
    rows = conn.execute("""
        SELECT a.name, ae.role
        FROM   actor_events ae
        JOIN   actors a ON a.actor_id = ae.actor_id
        JOIN   events e ON e.event_id = ae.event_id
        WHERE  e.title LIKE '%Pretoria March%'
        ORDER  BY a.name
    """).fetchall()
    for row in rows:
        print(f"    · {row['name']:<45} [{row['role']}]")

    # FTS5 search tests
    print("\n  FTS5 search: artifacts matching 'surveillance'")
    rows = conn.execute("""
        SELECT a.title, a.source
        FROM   artifacts_fts f
        JOIN   artifacts a ON a.artifact_id = f.rowid
        WHERE  artifacts_fts MATCH 'surveillance'
    """).fetchall()
    for row in rows:
        print(f"    · [{row['source']:>12}]  {row['title']}")

    print("\n  FTS5 search: events matching 'constitutional'")
    rows = conn.execute("""
        SELECT e.title, e.category
        FROM   events_fts f
        JOIN   events e ON e.event_id = f.rowid
        WHERE  events_fts MATCH 'constitutional'
    """).fetchall()
    for row in rows:
        print(f"    · [{row['category']:>14}]  {row['title']}")

    print("[FORGE] ─────────────────────────────────────────────────────\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not DB_PATH.exists():
        print(f"[seed] ERROR: Database not found at {DB_PATH}")
        print("[seed] Run  python app.py --init-db  first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="FORGE seed data loader")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe existing data before seeding (schema is preserved).",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")

    if args.reset:
        print("[seed] Resetting existing data …")
        reset_data(conn)

    print("[seed] Inserting actors …")
    print("[seed] Inserting events …")
    print("[seed] Inserting artifacts …")
    print("[seed] Inserting actor–event relationships …")
    seed(conn)

    print("[seed] Rebuilding FTS5 indices …")
    rebuild_fts(conn)

    verify(conn)
    conn.close()

    print("[seed] Seed complete.")
    print("[seed] Start the server:  python app.py --debug")


if __name__ == "__main__":
    main()
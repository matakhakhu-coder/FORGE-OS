from __future__ import annotations
import sqlite3
import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z"

        cur = conn.execute(
            "INSERT INTO actors (name, type, description, source_type, created_at, confidence_score, automated) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "National Institute for Communicable Diseases (South Africa)",
                "institution",
                "South African national public health institute responsible for communicable "
                "disease surveillance, including SADC regional pathogen reporting "
                "(HealthMap-tracked outbreaks: malaria, measles, rabies, COVID-19, hantavirus).",
                "live", now, 0.5, 0,
            ),
        )
        sa_actor_id = cur.lastrowid
        print("new actor_id:", sa_actor_id)

        conn.execute(
            "INSERT INTO entity_relationships "
            "(subject_actor_id, object_actor_id, relation_type, description, confidence, extraction_method, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                sa_actor_id, 52, "AFFILIATED_WITH",
                "South Africa is a SADC member state; NICD participates in SADC regional "
                "pathogen surveillance and health ministers reporting structures.",
                0.5, "manual", now,
            ),
        )

        conn.execute(
            "INSERT INTO signal_actors (signal_id, actor_id, role, created_at) VALUES (?,?,?,?)",
            ("a23d810e-c552-42de-b77c-2871bedd11d0", sa_actor_id, "subject", now),
        )

        conn.execute(
            "INSERT INTO case_actors (case_id, actor_id, note, pinned_at) VALUES (?,?,?,?)",
            (
                11, sa_actor_id,
                "Bridges SA domestic cluster to Case 11 international SADC/regional pathogen "
                "surveillance actors via SADC membership.",
                now,
            ),
        )

        conn.commit()
        print("OK")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

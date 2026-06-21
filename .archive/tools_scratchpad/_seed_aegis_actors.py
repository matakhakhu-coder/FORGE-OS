#!/usr/bin/env python3
from __future__ import annotations
"""
Seed health-security actors for Case 11 (Regional Pathogen Surveillance).
Creates key institutional actors, relationships between them, and wires them
to case 11 via case_actors.

Safe to re-run (INSERT OR IGNORE throughout).  Zero schema changes.
"""

import pathlib
import sqlite3
from datetime import datetime, timezone

ROOT    = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "database.db"

NOW = datetime.now(timezone.utc).isoformat()

# ── Actors to create ──────────────────────────────────────────────────────────
# Each entry: (name, type, confidence, description)
ACTORS = [
    (
        "World Health Organization",
        "institution",
        0.90,
        "UN specialised agency responsible for international public health. "
        "Primary authoritative source for disease outbreak news and global "
        "health emergency declarations.",
    ),
    (
        "CDC Health Alert Network",
        "institution",
        0.85,
        "US Centers for Disease Control and Prevention emergency broadcast "
        "system. Issues Health Alert Network (HAN) advisories for public "
        "health threats including international humanitarian health crises.",
    ),
    (
        "SADC Health Ministers Meeting",
        "government",
        0.75,
        "Southern African Development Community inter-governmental body "
        "coordinating regional health policy, disease surveillance, and "
        "cross-border health emergency response across 16 member states.",
    ),
    (
        "DRC Ministry of Health",
        "government",
        0.72,
        "Democratic Republic of Congo national health authority. DRC is the "
        "primary SADC-adjacent disease vector corridor — mpox, cholera, and "
        "Ebola outbreaks in eastern DRC directly affect SADC border zones.",
    ),
    (
        "ProMED Mail",
        "media",
        0.70,
        "Program for Monitoring Emerging Diseases. Open-access early-warning "
        "infectious disease surveillance system operated by the International "
        "Society for Infectious Diseases. Tier-3 noise layer in Project Aegis.",
    ),
    (
        "UNFPA Africa Regional Office",
        "institution",
        0.65,
        "UN Population Fund regional office covering sub-Saharan Africa. "
        "Monitors humanitarian health conditions including reproductive health "
        "crises across conflict-affected SADC-adjacent zones.",
    ),
]

# ── Relationships ─────────────────────────────────────────────────────────────
# (subject_name, object_name, relation_type, confidence, description)
RELATIONSHIPS = [
    (
        "CDC Health Alert Network",
        "World Health Organization",
        "AFFILIATED_WITH",
        0.85,
        "CDC HAN advisories routinely reference and coordinate with WHO "
        "Disease Outbreak News declarations on international health events.",
    ),
    (
        "ProMED Mail",
        "World Health Organization",
        "AFFILIATED_WITH",
        0.75,
        "ProMED serves as an early-warning noise layer feeding into the WHO "
        "Global Outbreak Alert and Response Network (GOARN).",
    ),
    (
        "World Health Organization",
        "DRC Ministry of Health",
        "INVESTIGATES",
        0.80,
        "WHO maintains a permanent country office in Kinshasa and leads "
        "outbreak investigation and response coordination in DRC — the "
        "highest-burden disease surveillance zone in the SADC corridor.",
    ),
    (
        "SADC Health Ministers Meeting",
        "DRC Ministry of Health",
        "LEADS",
        0.65,
        "SADC regional health coordination framework includes DRC as a "
        "priority surveillance partner given its role as the primary "
        "cross-border disease vector into eastern and southern Africa.",
    ),
    (
        "UNFPA Africa Regional Office",
        "SADC Health Ministers Meeting",
        "AFFILIATED_WITH",
        0.60,
        "UNFPA regional office coordinates with SADC on reproductive health "
        "emergency response and humanitarian health programming across "
        "conflict-affected member states.",
    ),
]


def run() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        # ── 1. Insert actors ──────────────────────────────────────────────────
        name_to_id: dict[str, int] = {}

        for name, atype, confidence, desc in ACTORS:
            existing = cur.execute(
                "SELECT actor_id FROM actors WHERE name = ?", (name,)
            ).fetchone()

            if existing:
                aid = existing["actor_id"]
                print(f"[aegis-actors] exists: {name} -> actor_id={aid}")
            else:
                cur.execute("""
                    INSERT INTO actors
                        (name, type, confidence_score, description,
                         source_type, created_at, automated)
                    VALUES (?, ?, ?, ?, 'live', ?, 0)
                """, (name, atype, confidence, desc, NOW))
                aid = cur.lastrowid
                print(f"[aegis-actors] created: {name} -> actor_id={aid}")

            name_to_id[name] = aid

        # ── 2. Insert relationships ────────────────────────────────────────────
        for subj_name, obj_name, rel, conf, desc in RELATIONSHIPS:
            subj_id = name_to_id[subj_name]
            obj_id  = name_to_id[obj_name]

            existing = cur.execute("""
                SELECT relationship_id FROM entity_relationships
                WHERE subject_actor_id = ? AND object_actor_id = ?
                  AND relation_type = ?
            """, (subj_id, obj_id, rel)).fetchone()

            if existing:
                print(f"[aegis-actors] rel exists: {subj_name} -{rel}-> {obj_name}")
            else:
                cur.execute("""
                    INSERT INTO entity_relationships
                        (subject_actor_id, object_actor_id, relation_type,
                         confidence, extraction_method, description, created_at)
                    VALUES (?, ?, ?, ?, 'manual', ?, ?)
                """, (subj_id, obj_id, rel, conf, desc, NOW))
                print(f"[aegis-actors] rel created: {subj_name} -{rel}-> {obj_name}")

        # ── 3. Link all actors to case 11 via case_actors ─────────────────────
        for name, aid in name_to_id.items():
            cur.execute("""
                INSERT OR IGNORE INTO case_actors (case_id, actor_id)
                VALUES (11, ?)
            """, (aid,))

        conn.commit()

        print(f"\n[aegis-actors] Summary")
        print(f"  actors created/verified : {len(ACTORS)}")
        print(f"  relationships           : {len(RELATIONSHIPS)}")
        print(f"  all linked to case_id=11")
        print(f"  actor_ids               : {list(name_to_id.values())}")

    finally:
        conn.close()


if __name__ == "__main__":
    run()

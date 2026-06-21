#!/usr/bin/env python3
from __future__ import annotations
"""
Wire Case #15 entities into graph + entity directory.
Creates actors, case_actors, and entity_relationships for the cyber case.
"""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

DB = pathlib.Path(__file__).parent.parent / "database.db"
CASE_ID = 15
NOW = datetime.now(timezone.utc).isoformat()


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # ── Create new actors ────────────────────────────────────────────
        new_actors = [
            {
                "name": "Friedrich Kraft",
                "type": "person",
                "description": (
                    "CEO of both 3xK Tech GmbH and Plainproxies. Identified by KrebsOnSecurity "
                    "(January 2026) as the operator of proxy infrastructure linked to the Kimwolf "
                    "botnet. Public routing records confirm he operates 3xK Tech GmbH from Germany. "
                    "Abuse contact: friedrich.kraeft@3xktech.cloud, +4917641256819."
                ),
                "source_type": "live",
                "confidence_score": 0.85,
            },
            {
                "name": "Plainproxies",
                "type": "organization",
                "description": (
                    "Residential proxy service operated by Friedrich Kraft. Distributed the ByteConnect "
                    "SDK through devices infected by the Kimwolf botnet (2 million Android TV boxes), "
                    "transforming them into proxy relays for malicious traffic. Investigated by "
                    "KrebsOnSecurity (January 2026)."
                ),
                "source_type": "live",
                "confidence_score": 0.80,
            },
            {
                "name": "Kimwolf Botnet",
                "type": "other",
                "description": (
                    "Botnet that infected over 2 million unofficial Android TV streaming boxes. "
                    "Compromised devices were enrolled as proxy relays via the ByteConnect SDK, "
                    "distributed through Plainproxies. Infrastructure operated under 3xK Tech GmbH "
                    "(AS200373). Investigated by KrebsOnSecurity (January 2026)."
                ),
                "source_type": "live",
                "confidence_score": 0.80,
            },
            {
                "name": "MEGA (mega.nz)",
                "type": "organization",
                "description": (
                    "Cloud storage and file hosting service headquartered in Auckland, New Zealand. "
                    "Offers 20GB free storage with end-to-end encryption. Has documented history of "
                    "credential stuffing attacks — 15,500+ users compromised in a known incident. "
                    "Target of the credential stuffing attack in Case #15."
                ),
                "source_type": "live",
                "confidence_score": 0.90,
            },
        ]

        actor_ids = {}

        # First, get existing actor #94 (3xK Tech GmbH)
        actor_ids["3xK Tech GmbH (AS200373)"] = 94

        for actor in new_actors:
            # Check if actor already exists
            existing = conn.execute(
                "SELECT actor_id FROM actors WHERE name = ?", (actor["name"],)
            ).fetchone()
            if existing:
                actor_ids[actor["name"]] = existing["actor_id"]
                print(f"  [=] Actor exists: #{existing['actor_id']} {actor['name']}")
            else:
                cur = conn.execute(
                    "INSERT INTO actors (name, type, description, source_type, confidence_score) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (actor["name"], actor["type"], actor["description"],
                     actor["source_type"], actor["confidence_score"]),
                )
                actor_ids[actor["name"]] = cur.lastrowid
                print(f"  [+] Actor #{cur.lastrowid}: {actor['name']} ({actor['type']})")

        # ── Wire ALL actors to Case #15 via case_actors ──────────────────
        for name, aid in actor_ids.items():
            conn.execute(
                "INSERT OR IGNORE INTO case_actors (case_id, actor_id) VALUES (?, ?)",
                (CASE_ID, aid),
            )
        print(f"\n  [+] Wired {len(actor_ids)} actors to case #{CASE_ID} via case_actors")

        # ── Create entity_relationships ──────────────────────────────────
        relationships = [
            # Friedrich Kraft leads both organizations
            (actor_ids["Friedrich Kraft"], actor_ids["3xK Tech GmbH (AS200373)"],
             "LEADS", "Friedrich Kraft is CEO of 3xK Tech GmbH (KrebsOnSecurity, Jan 2026)", "manual"),
            (actor_ids["Friedrich Kraft"], actor_ids["Plainproxies"],
             "LEADS", "Friedrich Kraft is CEO of Plainproxies (KrebsOnSecurity, Jan 2026)", "manual"),
            # 3xK Tech and Plainproxies are affiliated
            (actor_ids["3xK Tech GmbH (AS200373)"], actor_ids["Plainproxies"],
             "AFFILIATED_WITH", "Same CEO (Friedrich Kraft); shared infrastructure (AS200373)", "manual"),
            # Plainproxies operated the Kimwolf botnet proxy layer
            (actor_ids["Plainproxies"], actor_ids["Kimwolf Botnet"],
             "AFFILIATED_WITH", "Plainproxies distributed ByteConnect SDK via Kimwolf-infected devices", "manual"),
            # 3xK Tech targeted MEGA
            (actor_ids["3xK Tech GmbH (AS200373)"], actor_ids["MEGA (mega.nz)"],
             "ACCUSED_OF", "Credential stuffing attack against MEGA accounts from AS200373 infrastructure", "manual"),
        ]

        for src_id, tgt_id, rel_type, desc, method in relationships:
            conn.execute("""
                INSERT OR IGNORE INTO entity_relationships
                    (subject_actor_id, object_actor_id, relation_type, description, extraction_method)
                VALUES (?, ?, ?, ?, ?)
            """, (src_id, tgt_id, rel_type, desc, method))
            src_name = [k for k, v in actor_ids.items() if v == src_id][0]
            tgt_name = [k for k, v in actor_ids.items() if v == tgt_id][0]
            print(f"  [+] {src_name} --[{rel_type}]--> {tgt_name}")

        # ── Link new actors to existing signals via signal_actors ────────
        # Get all Case #15 signal IDs
        case_sigs = conn.execute(
            "SELECT signal_id FROM case_signals WHERE case_id = ?", (CASE_ID,)
        ).fetchall()
        sig_ids = [r["signal_id"] for r in case_sigs]

        # Link Friedrich Kraft and Plainproxies to the Krebs signal specifically
        krebs_sig = conn.execute(
            "SELECT signal_id FROM signals WHERE external_id = '3xktech-kimwolf-krebs-investigation'"
        ).fetchone()
        if krebs_sig:
            for actor_name in ["Friedrich Kraft", "Plainproxies", "Kimwolf Botnet"]:
                conn.execute(
                    "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                    (krebs_sig["signal_id"], actor_ids[actor_name], "mentioned"),
                )

        # Link MEGA to all signals
        for sid in sig_ids:
            conn.execute(
                "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                (sid, actor_ids["MEGA (mega.nz)"], "target"),
            )

        conn.commit()

        # ── Verify ───────────────────────────────────────────────────────
        ca_count = conn.execute(
            "SELECT COUNT(*) as c FROM case_actors WHERE case_id = ?", (CASE_ID,)
        ).fetchone()
        er_count = conn.execute("SELECT COUNT(*) as c FROM entity_relationships").fetchone()
        print(f"\n=== Verification ===")
        print(f"  case_actors for case #{CASE_ID}: {ca_count['c']}")
        print(f"  entity_relationships total: {er_count['c']}")
        print(f"  Actors wired: {list(actor_ids.items())}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

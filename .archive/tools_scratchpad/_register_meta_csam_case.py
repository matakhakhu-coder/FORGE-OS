#!/usr/bin/env python3
from __future__ import annotations
"""Register Meta CSAM case — event, actors, relationships, signals."""

import json
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

DB = pathlib.Path(__file__).parent.parent / "database.db"
NOW = datetime.now(timezone.utc).isoformat()


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        # ── Case ─────────────────────────────────────────────────────────
        conn.execute(
            "INSERT INTO cases (name, description, hypothesis, status, case_type, source_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "Meta Data Disclosure: SA Child Exploitation Network (Gauteng High Court)",
                (
                    "The Digital Law Company (DLC), led by Emma Sadleir, secured a consent order from "
                    "Judge Mudunwazi Makamu in the Gauteng Division of the High Court, Johannesburg, "
                    "compelling Meta to hand over subscriber data (phone numbers, IP addresses, names, "
                    "physical addresses) for 60+ offending accounts across Instagram and WhatsApp. "
                    "12 WhatsApp channels deleted, 58 Instagram accounts removed. Meta committed to a "
                    "two-year direct hotline with DLC for urgent child protection cases."
                ),
                (
                    "SA courts can compel global tech platforms to disclose user data in CSAM cases. "
                    "The consent order establishes a precedent that Meta can be held accountable in SA "
                    "jurisdiction. The 60+ accounts suggest an organized distribution network, not "
                    "isolated offenders. The two-year hotline commitment indicates Meta expects ongoing "
                    "case volume from SA."
                ),
                "active",
                "criminal",
                "live",
            ),
        )
        case_id = conn.execute("SELECT MAX(case_id) FROM cases").fetchone()[0]
        print(f"Case #{case_id} created")

        # ── Actors ───────────────────────────────────────────────────────
        actor_defs = [
            ("Meta Platforms Inc", "organization",
             "Global technology company operating Instagram and WhatsApp. Compelled by Gauteng High Court "
             "to disclose subscriber data for 60+ accounts distributing child sexual abuse material.",
             0.90),
            ("Digital Law Company (DLC)", "organization",
             "South African digital rights and social media law firm. Led the urgent High Court application "
             "against Meta. Founded/led by Emma Sadleir.",
             0.85),
            ("Emma Sadleir", "person",
             "South African social media law expert leading the Digital Law Company. Spearheaded the "
             "urgent High Court application against Meta for CSAM data disclosure.",
             0.85),
            ("Judge Mudunwazi Makamu", "person",
             "Judge of the Gauteng Division of the High Court, Johannesburg. Issued the consent order "
             "compelling Meta to disclose subscriber data in the CSAM case.",
             0.80),
            ("Film and Publications Board (FPB)", "government",
             "South African government regulatory body for content classification and online distribution "
             "regulation, including child sexual abuse material.",
             0.60),
        ]

        actor_ids = {}
        for name, atype, desc, conf in actor_defs:
            existing = conn.execute(
                "SELECT actor_id FROM actors WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                actor_ids[name] = existing["actor_id"]
                print(f"  [=] Actor exists: #{existing['actor_id']} {name}")
            else:
                conn.execute(
                    "INSERT INTO actors (name, type, description, source_type, confidence_score) "
                    "VALUES (?,?,?,?,?)",
                    (name, atype, desc, "live", conf),
                )
                aid = conn.execute("SELECT MAX(actor_id) FROM actors").fetchone()[0]
                actor_ids[name] = aid
                print(f"  [+] Actor #{aid}: {name} ({atype})")

        for name, aid in actor_ids.items():
            conn.execute(
                "INSERT OR IGNORE INTO case_actors (case_id, actor_id) VALUES (?,?)",
                (case_id, aid),
            )
        print(f"  [+] Linked {len(actor_ids)} actors to case #{case_id}")

        # ── Relationships ────────────────────────────────────────────────
        rels = [
            (actor_ids["Emma Sadleir"], actor_ids["Digital Law Company (DLC)"],
             "LEADS", "Emma Sadleir leads/founded the Digital Law Company"),
            (actor_ids["Digital Law Company (DLC)"], actor_ids["Meta Platforms Inc"],
             "LITIGATES_AGAINST", "DLC brought urgent High Court application against Meta for CSAM data disclosure"),
            (actor_ids["Judge Mudunwazi Makamu"], actor_ids["Meta Platforms Inc"],
             "INVESTIGATED_BY", "Judge Makamu issued consent order compelling Meta to disclose subscriber data"),
        ]
        for src, tgt, rel, desc in rels:
            conn.execute(
                "INSERT OR IGNORE INTO entity_relationships "
                "(subject_actor_id, object_actor_id, relation_type, description, extraction_method) "
                "VALUES (?, ?, ?, ?, ?)",
                (src, tgt, rel, desc, "manual"),
            )
            print(f"  [+] {rel}: {src} -> {tgt}")

        # ── Signals ──────────────────────────────────────────────────────
        signal_defs = [
            {
                "external_id": "citizen-meta-csam-sa-court-order-jul2025",
                "title": "Meta compelled to hand over user data in SA child pornography case -- Gauteng High Court consent order secured by Digital Law Company",
                "content": (
                    "The Digital Law Company (DLC), led by Emma Sadleir, secured a consent order from "
                    "Judge Mudunwazi Makamu in the Gauteng High Court compelling Meta to disclose "
                    "subscriber data (phone numbers, IP addresses, names, physical addresses) for 60+ "
                    "offending accounts across Instagram and WhatsApp that distributed child sexual abuse "
                    "material. 12 WhatsApp channels deleted, 58 Instagram accounts removed. Meta committed "
                    "to a two-year direct hotline with DLC for urgent child protection cases. "
                    "Legal team: Ben Winks, Sanan Mirzoyev, Rupert Candy, John Makate, Julian Govender "
                    "(Rupert Candy Attorneys). Sets precedent for SA jurisdiction over global tech platforms."
                ),
                "gravity": 0.85,
                "metadata": {
                    "source_url": "https://www.citizen.co.za/network-news/lnn/article/meta-hands-over-user-data-in-sa-child-pornography-case/",
                    "platforms": ["Instagram", "WhatsApp"],
                    "accounts_removed": {"whatsapp_channels": 12, "instagram_accounts": 58},
                    "data_disclosed": ["phone numbers", "IP addresses", "names", "physical addresses"],
                    "court": "Gauteng Division of the High Court, Johannesburg",
                    "judge": "Mudunwazi Makamu",
                },
            },
            {
                "external_id": "meta-csam-consent-order-precedent-analysis",
                "title": "ANALYSIS: Gauteng consent order establishes SA jurisdictional precedent over global tech platforms for CSAM data disclosure",
                "content": (
                    "The Meta consent order is structurally significant beyond the immediate CSAM case. "
                    "It establishes that: (1) SA courts can compel US-headquartered tech companies to "
                    "disclose subscriber data; (2) the Gauteng High Court has jurisdiction over Meta's "
                    "SA operations despite no local office; (3) consent orders can include ongoing "
                    "obligations (the two-year hotline); (4) the volume (60+ accounts) suggests organized "
                    "distribution, not isolated offenders. The two-year DLC-Meta hotline bypasses normal "
                    "MLAT channels -- significantly faster than the standard 6-18 month process."
                ),
                "gravity": 0.78,
                "metadata": {"signal_type": "analytical_assessment"},
            },
            {
                "external_id": "meta-csam-70-accounts-network-structure",
                "title": "Intelligence note: 70+ Instagram/WhatsApp accounts distributing CSAM suggests organized network with cross-platform coordination",
                "content": (
                    "58 Instagram accounts + 12 WhatsApp channels = 70 linked entities distributing "
                    "child sexual abuse material. The cross-platform nature (Instagram for discovery, "
                    "WhatsApp for distribution) indicates coordinated operation rather than isolated "
                    "offenders. Key questions: Are the 70 accounts operated by the same individual or a "
                    "network? Do the IP addresses cluster geographically? Are WhatsApp channels "
                    "payment-gated (commercial CSAM)? Do disclosed phone numbers link to known offender "
                    "databases? Are there SAFLII precedents for cross-platform takedown orders?"
                ),
                "gravity": 0.72,
                "metadata": {"signal_type": "intelligence_note"},
            },
        ]

        for sig in signal_defs:
            g = sig["gravity"]
            sid = str(uuid.uuid4())
            cur = conn.execute(
                "INSERT OR IGNORE INTO signals "
                "(signal_id, source, external_id, title, content, "
                "lat, lng, stream, status, metadata_json, is_priority, timestamp, "
                "gravity_score, published_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sid, "forge_incident", sig["external_id"],
                    sig["title"], sig["content"],
                    -26.2023, 28.0436,
                    "CRIME_INTEL", "promoted",
                    json.dumps(sig["metadata"]),
                    1, NOW, g, NOW,
                ),
            )
            if cur.rowcount > 0:
                conn.execute(
                    "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?,?,?)",
                    (case_id, sid, "Manual registration"),
                )
                for aid in actor_ids.values():
                    conn.execute(
                        "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?,?,?)",
                        (sid, aid, "mentioned"),
                    )
                print(f"  [+] G {g:.2f} | {sig['title'][:70]}...")

        conn.commit()

        total_sigs = conn.execute(
            "SELECT COUNT(*) FROM case_signals WHERE case_id = ?", (case_id,)
        ).fetchone()[0]
        total_acts = conn.execute(
            "SELECT COUNT(*) FROM case_actors WHERE case_id = ?", (case_id,)
        ).fetchone()[0]
        print(f"\n{'='*60}")
        print(f"Case #{case_id} registered")
        print(f"  Signals: {total_sigs}")
        print(f"  Actors:  {total_acts}")
        print(f"  Relationships: 3")
        print(f"{'='*60}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

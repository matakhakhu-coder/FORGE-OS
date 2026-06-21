#!/usr/bin/env python3
from __future__ import annotations
"""Ingest escalation signals for MEGA breach — two additional attacker IPs."""

import json
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

DB = pathlib.Path(__file__).parent.parent / "database.db"
CASE_ID = 15
ACTOR_ID = 94

SIGNALS = [
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "mega-breach-104.207.47.133-20260619",
        "title": "Second unauthorized MEGA session: 104.207.47.133 (3xK Tech GmbH / AS200373, Ashburn VA)",
        "content": json.dumps({
            "summary": (
                "Second unauthorized MEGA session detected at 2026-06-19 16:19 SAST from IP 104.207.47.133. "
                "User-Agent: Unknown. Same operator as original breach IP: 3xK Tech GmbH (AS200373). "
                "IPinfo classification: Hosting provider, Proxy detected, SSH enabled. "
                "Location: Ashburn, Virginia, USA. Abuse contact: friedrich.kraeft@3xktech.cloud. "
                "This IP is in a different /24 block (104.207.47.0/24) from the original (209.50.171.0/24) "
                "but same ASN — confirms multi-node proxy cluster operation."
            ),
            "ip": "104.207.47.133",
            "asn": "AS200373",
            "org": "3xK Tech GmbH",
            "geo": {"city": "Ashburn", "region": "Virginia", "country": "US"},
            "ipinfo_tags": ["hosting", "proxy", "ssh"],
            "user_agent": "Unknown",
            "network_range": "104.207.47.0/24",
        }),
        "stream": "CRIME_INTEL",
        "timestamp": "2026-06-19T16:19:00+02:00",
        "status": "promoted",
        "metadata_json": json.dumps({
            "incident_type": "account_compromise",
            "source_ip": "104.207.47.133",
            "source_asn": "AS200373",
            "severity": "HIGH",
        }),
        "is_priority": 1,
        "lat": 39.0437,
        "lng": -77.4875,
        "gravity": 0.72,
    },
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "mega-breach-209.50.190.92-megaapiclient-20260619",
        "title": "CRITICAL: MegaApiClient session from 209.50.190.92 (3xK Tech GmbH / AS200373, Berlin) — active data exfiltration",
        "content": json.dumps({
            "summary": (
                "CRITICAL — Active data exfiltration session detected at 2026-06-19 16:19 SAST. "
                "IP: 209.50.190.92. Client: MegaApiClient (MEGA SDK/API client for programmatic "
                "bulk file access — NOT a browser session). Same operator: 3xK Tech GmbH (AS200373). "
                "IPinfo classification: Hosting provider, Proxy detected, SSH enabled. "
                "Location: Berlin, Germany (MEGA reports as France — geo database discrepancy). "
                "MegaApiClient indicates the attacker is using the MEGA API to programmatically "
                "download files — this is bulk exfiltration, not browsing. "
                "Attack sequence: (1) Go-http-client login test at 15:53 from 209.50.171.190, "
                "(2) Two new sessions at 16:19 from different nodes in the same AS200373 cluster — "
                "one Unknown client from Ashburn, one MegaApiClient from Berlin. "
                "All three IPs: same ASN, same abuse contact (friedrich.kraeft@3xktech.cloud), "
                "all classified as hosting/proxy by IPinfo. "
                "This is a coordinated multi-node credential stuffing + exfiltration operation."
            ),
            "ip": "209.50.190.92",
            "asn": "AS200373",
            "org": "3xK Tech GmbH",
            "geo": {"city": "Berlin", "region": "Berlin", "country": "DE"},
            "ipinfo_tags": ["hosting", "proxy", "ssh"],
            "user_agent": "MegaApiClient",
            "network_range": "209.50.190.0/24",
            "exfiltration_indicator": True,
            "attack_sequence": [
                "15:53 — 209.50.171.190 (Ashburn) — Go-http-client/1.1 — credential test",
                "16:19 — 104.207.47.133 (Ashburn) — Unknown — second session",
                "16:19 — 209.50.190.92 (Berlin) — MegaApiClient — API bulk download",
            ],
        }),
        "stream": "CRIME_INTEL",
        "timestamp": "2026-06-19T16:19:00+02:00",
        "status": "promoted",
        "metadata_json": json.dumps({
            "incident_type": "data_exfiltration",
            "source_ip": "209.50.190.92",
            "source_asn": "AS200373",
            "client": "MegaApiClient",
            "severity": "CRITICAL",
        }),
        "is_priority": 1,
        "lat": 52.5244,
        "lng": 13.4105,
        "gravity": 0.92,
    },
]


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        inserted = 0
        for sig in SIGNALS:
            g = sig.pop("gravity")
            cur = conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, metadata_json,
                     is_priority, stream)
                VALUES
                    (:signal_id, :source, :external_id, :title, :content,
                     :lat, :lng, :timestamp, :status, :metadata_json,
                     :is_priority, :stream)
            """, sig)
            if cur.rowcount > 0:
                inserted += 1
                conn.execute(
                    "UPDATE signals SET gravity_score = ? WHERE signal_id = ?",
                    (g, sig["signal_id"]),
                )
                print(f"  [+] G {g:.2f} | {sig['title'][:75]}...")
            else:
                print(f"  [=] exists: {sig['external_id']}")

        # Resolve all signal IDs for this case
        ext_ids = [s["external_id"] for s in SIGNALS]
        ph = ",".join("?" * len(ext_ids))
        rows = conn.execute(
            f"SELECT signal_id FROM signals WHERE external_id IN ({ph})", ext_ids
        ).fetchall()

        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)",
                (CASE_ID, r["signal_id"], "Escalation: multi-node attack detected"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                (r["signal_id"], ACTOR_ID, "threat_infrastructure"),
            )

        conn.commit()
        print(f"\nInserted: {inserted} | Linked to case #{CASE_ID}, actor #{ACTOR_ID}")

        # Summary
        total = conn.execute(
            "SELECT COUNT(*) as c FROM case_signals WHERE case_id = ?", (CASE_ID,)
        ).fetchone()
        print(f"Total signals on case #{CASE_ID}: {total['c']}")

        all_sigs = conn.execute(
            "SELECT gravity_score, title FROM signals WHERE source = 'forge_incident' ORDER BY gravity_score DESC"
        ).fetchall()
        print("\nAll incident signals:")
        for s in all_sigs:
            g = s["gravity_score"] or 0
            print(f"  G {g:.2f} | {s['title'][:75]}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

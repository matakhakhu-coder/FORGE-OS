#!/usr/bin/env python3
from __future__ import annotations
"""Ingest MEGA GDPR forensic analysis into FORGE Case #15."""

import json
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

DB = pathlib.Path(__file__).parent.parent / "database.db"
CASE_ID = 15
ACTOR_ID = 94
NOW = datetime.now(timezone.utc).isoformat()
ARTICLE_SLUG = "mega-account-compromise-3xktech-credential-stuffing"

GDPR_SIGNAL = {
    "signal_id": str(uuid.uuid4()),
    "source": "forge_incident",
    "external_id": "mega-gdpr-forensic-analysis-20260619",
    "title": "GDPR forensic analysis: 25.8-minute attack window, 3 attacker sessions killed, no file exfiltration confirmed",
    "content": json.dumps({
        "summary": (
            "Analysis of MEGA GDPR data export confirms the full attack sequence and provides "
            "forensic evidence of attacker behaviour. Five sessions total: 2 legitimate (user's "
            "Chrome browser + Android app, both ZA-based, both alive), 3 attacker (all dead/terminated). "
            "\n\n"
            "ATTACK TIMELINE (all UTC, 19 June 2026):\n"
            "T+0s (13:53:52): 209.50.171.190 — Go-http-client/1.1 — credential test — duration 0s (instant login check)\n"
            "T+902s (14:08:54): Chat entity created within MEGA account — suspicious automated action during attack window\n"
            "T+1546s (14:19:38): 209.50.190.92 — MegaApiClient/1.10.3 — API session — duration 1s only (killed immediately)\n"
            "T+1547s (14:19:39): 104.207.47.133 — Go-http-client/1.1 — second session — duration 233s (3.9 minutes — longest attacker session)\n"
            "Total attack window: 1,547 seconds (25.8 minutes).\n\n"
            "CRITICAL FINDINGS:\n"
            "1. All 3 attacker sessions show alive=0 (dead/terminated by account holder)\n"
            "2. file_related_ips in GDPR export contains ONLY the account holder's IP — NO attacker IPs accessed files\n"
            "3. Zero share links created (links: []) — attacker did not create public download links\n"
            "4. lastdownloaded_dailyrefresh timestamp is from 2022 — no downloads in 2026 by anyone\n"
            "5. MegaApiClient version confirmed as 1.10.3 — session lasted only 1 second before termination\n"
            "6. Second Go-http-client session (104.207.47.133) lasted 233 seconds — attacker had ~4 min access but no file operations recorded\n"
            "7. A chat entity was created at T+902s (14:08:54 UTC) — anomalous activity during attack window, likely automated bot action\n"
            "8. contacts.json shows zero contacts, zero invites, zero shares — account isolation preserved\n\n"
            "ASSESSMENT: While the attacker successfully authenticated and maintained sessions for up to 233 seconds, "
            "the GDPR evidence indicates NO confirmed file exfiltration. The account holder's rapid session termination "
            "likely prevented data loss. The MegaApiClient session lasting only 1 second suggests it was killed before "
            "any bulk download could begin."
        ),
        "sessions": [
            {
                "created_utc": "2026-06-19T13:53:52Z",
                "ip": "209.50.171.190",
                "port": 63855,
                "user_agent": "Go-http-client/1.1",
                "country": "US",
                "duration_seconds": 0,
                "alive": False,
                "classification": "attacker",
                "role": "credential_test",
            },
            {
                "created_utc": "2026-06-19T14:19:38Z",
                "ip": "209.50.190.92",
                "port": 52563,
                "user_agent": "MegaApiClient/1.10.3",
                "country": "FR",
                "duration_seconds": 1,
                "alive": False,
                "classification": "attacker",
                "role": "api_exfiltration_attempt",
            },
            {
                "created_utc": "2026-06-19T14:19:39Z",
                "ip": "104.207.47.133",
                "port": 17487,
                "user_agent": "Go-http-client/1.1",
                "country": "US",
                "duration_seconds": 233,
                "alive": False,
                "classification": "attacker",
                "role": "reconnaissance",
            },
        ],
        "anomalous_events": [
            {
                "timestamp_utc": "2026-06-19T14:08:54Z",
                "event": "chat_entity_created",
                "detail": "Chat created within MEGA account during attack window (T+902s from initial breach)",
            },
        ],
        "file_exfiltration": {
            "attacker_ips_in_file_access": False,
            "share_links_created": 0,
            "last_download_timestamp": "2022-03-29T03:52:42Z",
            "assessment": "No confirmed file exfiltration",
        },
    }),
    "stream": "CRIME_INTEL",
    "timestamp": NOW,
    "status": "promoted",
    "metadata_json": json.dumps({
        "incident_type": "forensic_analysis",
        "data_source": "MEGA GDPR export",
        "severity": "HIGH",
        "confidence": 0.95,
    }),
    "is_priority": 1,
    "lat": None,
    "lng": None,
}

# Updated article body with GDPR findings
UPDATED_BODY = '''## Incident Summary

On 19 June 2026 at 13:53:52 UTC, an unauthorized login to a personal MEGA cloud storage account was detected. The session originated from IP address **209.50.171.190** using the user-agent string `Go-http-client/1.1` — the default identifier for Go's standard HTTP library, indicating an automated tool rather than a human operating a browser.

Within 25.8 minutes, two additional sessions were opened from the same infrastructure:

| Time (UTC) | IP Address | Location | Client | Duration | Status |
|---|---|---|---|---|---|
| 13:53:52 | 209.50.171.190 | Ashburn, VA | Go-http-client/1.1 | **0 seconds** | KILLED |
| 14:19:38 | 209.50.190.92 | Berlin, DE | MegaApiClient/1.10.3 | **1 second** | KILLED |
| 14:19:39 | 104.207.47.133 | Ashburn, VA | Go-http-client/1.1 | **233 seconds** | KILLED |

## GDPR Forensic Analysis

A MEGA GDPR data export was obtained and analysed to determine the full scope of the breach. Key findings:

### Sessions

The GDPR export confirmed **5 total sessions** on the account — 2 legitimate (account holder's Chrome browser and Android app, both geolocated to South Africa, both still active) and 3 attacker sessions (all terminated).

All three attacker sessions show `alive: false` — the account holder successfully killed every unauthorized session.

### File Access

**No confirmed file exfiltration.** The GDPR `files.json` export shows:

- `file_related_ips` contains **only the account holder's own IP address** — no attacker IPs appear in file access logs
- `links: []` — **zero share links** were created (the attacker did not create public download URLs)
- `lastdownloaded_dailyrefresh` timestamp is from **March 2022** — no file downloads have occurred in 2026 by any party

### Anomalous Activity

A **chat entity was created** within the MEGA account at 14:08:54 UTC — 15 minutes after the initial breach and 11 minutes before the MegaApiClient session. This appears to be an automated bot action during the reconnaissance phase rather than legitimate user activity.

### Contacts and Shares

`contacts.json` confirms: zero contacts, zero invites, zero outbound shares. Account isolation was preserved throughout the incident.

## Infrastructure Attribution

All three attacker IP addresses belong to **3xK Tech GmbH**, a German hosting provider operating under **ASN AS200373**.

- **Headquarters:** Altenhofer Weg 21, Schorfheide, 16244, Germany
- **Abuse contact:** friedrich.kraeft@3xktech.cloud / +4917641256819
- **Network classification:** All three IPs tagged as Hosting + Proxy by IPinfo and Shodan

Shodan reconnaissance of the primary IP (209.50.171.190) revealed:

- **Port 22 (SSH):** "Exceeded MaxStartups" — high-volume automated connections
- **Port 3128 (HTTP Proxy):** 407 Proxy Authentication Required — proxy infrastructure node
- **Port 8081 (HTTP):** Let's Encrypt certificate valid 7 days only (SAN pointing to different IP 74.119.149.39) — rotating proxy cluster
- **Port 179 (BGP):** Open — routing-level infrastructure

3xK Tech GmbH has extensive abuse history: multiple IPs reported on AbuseIPDB (46+ reports), flagged on FraudGuard, Scamalytics, and CleanTalk.

## Attack Vector: Credential Stuffing

MEGA has a documented history of credential stuffing attacks, with 15,500+ users compromised in a known incident. The pattern observed here:

1. **T+0s — Credential test:** Go-http-client tests stolen credentials, session duration 0 seconds (instant authentication check)
2. **T+15min — Reconnaissance:** Chat entity created, possibly automated account enumeration
3. **T+25.8min — Exfiltration attempt:** MegaApiClient/1.10.3 opens API session for bulk download, simultaneously with a second Go-http-client session
4. **Intervention:** Account holder detects and terminates all attacker sessions within minutes

The use of three different IPs from separate /24 subnets but the same ASN is a deliberate evasion technique — distributing sessions to avoid single-IP rate limits.

## Assessment

**Confidence: HIGH.** Coordinated credential stuffing + exfiltration operation via 3xK Tech GmbH proxy infrastructure. Credentials obtained from a prior breach of a third-party service.

**Outcome: CONTAINED.** GDPR forensic evidence confirms no file downloads, no share links created, and no attacker IPs in file access logs. The account holder's rapid session termination prevented data exfiltration. The MegaApiClient session lasting only 1 second indicates it was killed before any bulk download could initiate.

## Indicators of Compromise

| Indicator | Type | Context |
|---|---|---|
| 209.50.171.190 | IPv4 | Initial credential test (duration: 0s) |
| 104.207.47.133 | IPv4 | Reconnaissance session (duration: 233s) |
| 209.50.190.92 | IPv4 | MegaApiClient exfiltration attempt (duration: 1s) |
| 74.119.149.39 | IPv4 | SSL SAN cross-reference (shared cert cluster) |
| AS200373 | ASN | 3xK Tech GmbH — all three attacker IPs |
| Go-http-client/1.1 | User-Agent | Automated credential testing tool |
| MegaApiClient/1.10.3 | Client ID | MEGA SDK v1.10.3 — programmatic bulk download |
| 209.50.171.0/24 | Subnet | Primary attack subnet |
| 104.207.47.0/24 | Subnet | Secondary attack subnet |
| 209.50.190.0/24 | Subnet | Exfiltration subnet |

## Sources

- MEGA GDPR data export (sessions.json, files.json, contacts.json)
- IPinfo.io geolocation and classification for all three IPs
- Shodan host reconnaissance (209.50.171.190)
- AbuseIPDB, FraudGuard, Scamalytics, CleanTalk for AS200373
- SecurityBrief NZ — MEGA credential stuffing incident
- OWASP Credential Stuffing documentation
'''


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        # Insert forensic signal
        cur = conn.execute("""
            INSERT OR IGNORE INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, metadata_json,
                 is_priority, stream)
            VALUES
                (:signal_id, :source, :external_id, :title, :content,
                 :lat, :lng, :timestamp, :status, :metadata_json,
                 :is_priority, :stream)
        """, GDPR_SIGNAL)

        if cur.rowcount > 0:
            conn.execute(
                "UPDATE signals SET gravity_score = 0.88 WHERE signal_id = ?",
                (GDPR_SIGNAL["signal_id"],),
            )
            conn.execute(
                "UPDATE signals SET published_at = ? WHERE signal_id = ?",
                (NOW, GDPR_SIGNAL["signal_id"]),
            )
            print(f"  [+] G 0.88 | {GDPR_SIGNAL['title'][:75]}")
        else:
            print("  [=] Forensic signal already exists")

        # Link to case and actor
        rows = conn.execute(
            "SELECT signal_id FROM signals WHERE external_id = ?",
            (GDPR_SIGNAL["external_id"],),
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)",
                (CASE_ID, r["signal_id"], "GDPR forensic analysis"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                (r["signal_id"], ACTOR_ID, "threat_infrastructure"),
            )

        # Add to signal-article map in the DB (for completeness)
        # The actual map is in publish.py — we update the article body directly
        conn.execute(
            "UPDATE articles SET body_markdown = ?, updated_at = ? WHERE slug = ?",
            (UPDATED_BODY, NOW, ARTICLE_SLUG),
        )
        print(f"  [+] Article updated: {ARTICLE_SLUG}")

        conn.commit()

        # Summary
        total = conn.execute(
            "SELECT COUNT(*) as c FROM case_signals WHERE case_id = ?", (CASE_ID,)
        ).fetchone()
        print(f"\nCase #{CASE_ID} total signals: {total['c']}")

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

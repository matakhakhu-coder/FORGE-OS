#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE Incident Ingestion — MEGA Account Breach (2026-06-19)
Wires threat intelligence on IP 209.50.171.190 into FORGE as:
  - 3 signals (intrusion event, infrastructure profile, threat actor profile)
  - 1 case (MEGA Account Compromise)
  - 1 actor (3xK Tech GmbH / AS200373 proxy infrastructure)
  - case_signals + signal_actors linkages

Usage:
    python tools/ingest_breach_incident.py
"""

import json
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "database.db"

NOW = datetime.now(timezone.utc).isoformat()
INCIDENT_TS = "2026-06-19T15:53:00+02:00"

# ── Signal definitions ───────────────────────────────────────────────────────

SIGNALS = [
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "mega-breach-209.50.171.190-20260619",
        "title": "Unauthorized MEGA account access from datacenter proxy IP 209.50.171.190 (3xK Tech GmbH / AS200373)",
        "content": json.dumps({
            "summary": (
                "Automated login to user MEGA cloud storage account detected at 2026-06-19 15:53 SAST. "
                "User-Agent: Go-http-client/1.1 (Go standard library HTTP client — automated tool, not browser). "
                "Source IP: 209.50.171.190, registered to 3xK Tech GmbH (AS200373), a German hosting provider "
                "operating datacenter infrastructure in Ashburn, Virginia, USA. "
                "Shodan profile shows: HTTP proxy on port 3128 (407 auth required), SSH on port 22 "
                "(Exceeded MaxStartups — indicative of high-volume automated connections), BGP on port 179, "
                "HTTP on port 8081 with 7-day Let's Encrypt cert (SAN: 74.119.149.39 — different IP, "
                "indicating shared proxy cluster). Tagged as 'Proxy' on Shodan. "
                "3xK Tech GmbH has multiple IPs reported on AbuseIPDB (46+ reports on other IPs), "
                "flagged on FraudGuard, Scamalytics, and CleanTalk blacklists. "
                "Assessment: Credential stuffing attack from proxy infrastructure node. "
                "Attacker likely obtained credentials from a previous breach of another service."
            ),
            "ip": "209.50.171.190",
            "asn": "AS200373",
            "org": "3xK Tech GmbH",
            "abuse_contact": "friedrich.kraeft@3xktech.cloud",
            "abuse_phone": "+4917641256819",
            "abuse_address": "Altenhofer Weg 21, Schorfheide, 16244, Germany",
            "user_agent": "Go-http-client/1.1",
            "geo": {"city": "Ashburn", "region": "Virginia", "country": "US", "lat": 39.0437, "lng": -77.4875},
            "open_ports": {
                "22/tcp": "SSH — Exceeded MaxStartups (high-volume automated connections)",
                "179/tcp": "BGP — unusual for a single server",
                "3128/tcp": "HTTP Proxy — 407 Proxy Authentication Required",
                "8081/tcp": "HTTP — Let's Encrypt cert (SAN: 74.119.149.39, valid 2026-06-12 to 2026-06-19)",
            },
            "shodan_tags": ["proxy"],
            "threat_indicators": [
                "Go-http-client/1.1 = automated tool, not human browser session",
                "Datacenter IP (hosting provider) — not residential",
                "HTTP proxy running on port 3128 — proxy infrastructure node",
                "SSH MaxStartups exceeded — high connection volume / botnet behavior",
                "7-day cert rotation with SAN pointing to different IP — shared proxy cluster",
                "3xK Tech GmbH: multiple IPs on AbuseIPDB, FraudGuard, Scamalytics, CleanTalk",
            ],
            "ssl_cert": {
                "issuer": "Let's Encrypt (CN=YE2)",
                "valid_from": "2026-06-12",
                "valid_to": "2026-06-19",
                "algorithm": "ECDSA with SHA384",
                "san_ip": "74.119.149.39",
            },
            "mega_context": (
                "MEGA has documented history of credential stuffing attacks — "
                "15,000+ users affected in a known incident. Attackers use stolen "
                "username/password pairs from other breaches with automated tools."
            ),
        }),
        "stream": "CRIME_INTEL",
        "timestamp": INCIDENT_TS,
        "status": "promoted",
        "metadata_json": json.dumps({
            "incident_type": "account_compromise",
            "target_service": "MEGA (mega.nz)",
            "attack_vector": "credential_stuffing",
            "source_ip": "209.50.171.190",
            "source_asn": "AS200373",
            "source_org": "3xK Tech GmbH",
            "user_agent": "Go-http-client/1.1",
            "severity": "HIGH",
            "confidence": 0.85,
        }),
        "is_priority": 1,
        "lat": 39.0437,
        "lng": -77.4875,
    },
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "3xktech-as200373-infra-profile",
        "title": "Threat infrastructure profile: 3xK Tech GmbH (AS200373) — proxy cluster with abuse history",
        "content": json.dumps({
            "summary": (
                "3xK Tech GmbH is a German hosting provider (ASN AS200373) headquartered at "
                "Altenhofer Weg 21, Schorfheide, 16244, Germany. Contact: friedrich.kraeft@3xktech.cloud. "
                "The network operates datacenter infrastructure in Ashburn, Virginia. "
                "Multiple IPs in the AS200373 range have been reported on AbuseIPDB (46+ reports "
                "on individual IPs), flagged on FraudGuard as a hosting provider threat, "
                "listed on Scamalytics fraud check, and indexed in CleanTalk spam blacklists. "
                "IP 209.50.171.190 specifically: Shodan-tagged as 'Proxy', runs HTTP proxy "
                "on port 3128, SSH hitting MaxStartups, BGP on 179, and short-lived Let's Encrypt "
                "certs with cross-IP SANs — consistent with a rotating proxy infrastructure cluster. "
                "RPKI status: valid Route Origin Authorization."
            ),
            "related_ip": "209.50.171.190",
            "related_san_ip": "74.119.149.39",
            "asn": "AS200373",
            "org": "3xK Tech GmbH",
            "network_range": "209.50.171.0/24",
        }),
        "stream": "CRIME_INTEL",
        "timestamp": NOW,
        "status": "promoted",
        "metadata_json": json.dumps({
            "signal_type": "infrastructure_profile",
            "asn": "AS200373",
        }),
        "is_priority": 0,
        "lat": 39.0437,
        "lng": -77.4875,
    },
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "mega-credential-stuffing-context",
        "title": "MEGA credential stuffing precedent: 15,000+ users compromised via automated login tools",
        "content": json.dumps({
            "summary": (
                "MEGA (mega.nz) has a documented history of credential stuffing attacks. "
                "In a known incident, 15,500 users were compromised when attackers tested "
                "stolen username/password pairs from other breaches against MEGA's login. "
                "Credential stuffing uses automated tools (Selenium, cURL, Go HTTP clients, "
                "PhantomJS) to test breached credentials across multiple services. "
                "Attackers distribute requests across proxy networks, rotate user agents, "
                "and use datacenter IPs to avoid rate limiting. "
                "The Go-http-client/1.1 user agent observed in this incident is the default "
                "for Go's net/http package — a common choice for credential stuffing tools "
                "due to Go's efficient HTTP/2 support and low resource usage. "
                "Source: securitybrief.co.nz, OWASP Credential Stuffing documentation."
            ),
        }),
        "stream": "CRIME_INTEL",
        "timestamp": NOW,
        "status": "promoted",
        "metadata_json": json.dumps({
            "signal_type": "contextual_intelligence",
            "topic": "credential_stuffing_precedent",
        }),
        "is_priority": 0,
        "lat": None,
        "lng": None,
    },
]

# ── Case definition ──────────────────────────────────────────────────────────

CASE = {
    "name": "MEGA Account Compromise — Credential Stuffing via 3xK Tech Proxy Infrastructure",
    "description": (
        "Unauthorized access to personal MEGA cloud storage account detected 2026-06-19 15:53 SAST. "
        "Attacker used automated Go HTTP client from datacenter IP 209.50.171.190 (3xK Tech GmbH, "
        "AS200373, Ashburn VA). Infrastructure profile indicates a proxy cluster node with extensive "
        "abuse history. Attack vector assessed as credential stuffing — password reuse from a prior "
        "breach of another service."
    ),
    "hypothesis": (
        "Credentials were obtained from a third-party data breach and tested against MEGA via "
        "automated tooling running on 3xK Tech GmbH proxy infrastructure. The attacker's goal "
        "is likely data exfiltration from MEGA cloud storage. Secondary hypothesis: the compromised "
        "credentials may be in active circulation on credential markets."
    ),
    "status": "active",
    "case_type": "cyber",
    "source_type": "live",
}

# ── Actor definition ─────────────────────────────────────────────────────────

ACTOR = {
    "name": "3xK Tech GmbH (AS200373)",
    "type": "organization",
    "description": (
        "German hosting provider operating datacenter infrastructure under ASN AS200373. "
        "Headquartered at Altenhofer Weg 21, Schorfheide, 16244, Germany. "
        "Abuse contact: friedrich.kraeft@3xktech.cloud, +4917641256819. "
        "Multiple IPs flagged on AbuseIPDB, FraudGuard, Scamalytics, and CleanTalk. "
        "IP 209.50.171.190 identified as proxy infrastructure node (Shodan-tagged 'Proxy') "
        "used in credential stuffing attack against MEGA account on 2026-06-19."
    ),
    "source_type": "live",
}


def main():
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # ── Insert signals ───────────────────────────────────────────────
        inserted = 0
        for sig in SIGNALS:
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
                print(f"  [+] Signal: {sig['title'][:80]}...")
            else:
                print(f"  [=] Signal exists (dedup): {sig['external_id']}")

        print(f"\nSignals: {inserted} inserted, {len(SIGNALS) - inserted} skipped")

        # ── Insert case ──────────────────────────────────────────────────
        cur = conn.execute(
            "INSERT INTO cases (name, description, hypothesis, status, case_type, source_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (CASE["name"], CASE["description"], CASE["hypothesis"],
             CASE["status"], CASE["case_type"], CASE["source_type"]),
        )
        case_id = cur.lastrowid
        print(f"\n  [+] Case #{case_id}: {CASE['name'][:60]}...")

        # ── Insert actor ─────────────────────────────────────────────────
        cur = conn.execute(
            "INSERT INTO actors (name, type, description, source_type) VALUES (?, ?, ?, ?)",
            (ACTOR["name"], ACTOR["type"], ACTOR["description"], ACTOR["source_type"]),
        )
        actor_id = cur.lastrowid
        print(f"  [+] Actor #{actor_id}: {ACTOR['name']}")

        # ── Resolve actual signal_ids from DB (handles UUID regeneration on re-run)
        ext_ids = [sig["external_id"] for sig in SIGNALS]
        placeholders = ",".join("?" * len(ext_ids))
        db_signals = conn.execute(
            f"SELECT signal_id, external_id FROM signals WHERE external_id IN ({placeholders})",
            ext_ids,
        ).fetchall()
        resolved_ids = [row["signal_id"] for row in db_signals]
        print(f"  [i] Resolved {len(resolved_ids)} signal IDs from DB")

        # ── Wire signals → case ──────────────────────────────────────────
        for sid in resolved_ids:
            conn.execute(
                "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)",
                (case_id, sid, "Auto-pinned by incident ingestion script"),
            )
        print(f"  [+] Pinned {len(resolved_ids)} signals to case #{case_id}")

        # ── Wire signals → actor ─────────────────────────────────────────
        for sid in resolved_ids:
            conn.execute(
                "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                (sid, actor_id, "threat_infrastructure"),
            )
        print(f"  [+] Linked {len(resolved_ids)} signals to actor #{actor_id}")

        conn.commit()
        print(f"\n{'='*60}")
        print(f"INCIDENT INGESTED SUCCESSFULLY")
        print(f"  Case ID:  {case_id}")
        print(f"  Actor ID: {actor_id}")
        print(f"  Signals:  {len(SIGNALS)}")
        print(f"{'='*60}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

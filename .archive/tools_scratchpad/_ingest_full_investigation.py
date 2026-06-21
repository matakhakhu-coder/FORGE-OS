#!/usr/bin/env python3
from __future__ import annotations
"""Full FORGE investigation run — credential stuffing context, 3xK/Kimwolf, stealer log hypothesis."""

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

SIGNALS = [
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "3xktech-kimwolf-krebs-investigation",
        "title": "KREBS: 3xK Tech GmbH CEO Friedrich Kraft also runs Plainproxies — Kimwolf botnet proxy service; Cloudflare named 3xK as Internet's #1 DDoS source (Jul 2025)",
        "content": json.dumps({
            "summary": (
                "Brian Krebs (KrebsOnSecurity) investigation published January 2026 links 3xK Tech GmbH "
                "directly to the Kimwolf botnet infrastructure. Key findings: "
                "\n\n"
                "1. Friedrich Kraft, CEO of 3xK Tech GmbH, is also CEO of Plainproxies — a residential "
                "proxy service that distributed the ByteConnect SDK through compromised devices.\n"
                "2. The ByteConnect SDK was installed on devices infected by the Kimwolf botnet (2 million "
                "unofficial Android TV streaming boxes), transforming them into proxy relays for malicious traffic.\n"
                "3. In July 2025, Cloudflare reported that 3xK Tech (a.k.a. Drei-K-Tech) had become "
                "'the Internet's largest source of application-layer DDoS attacks.'\n"
                "4. In November 2025, 3xK Tech IP addresses were responsible for approximately 75% of "
                "Internet scanning for a critical Palo Alto Networks vulnerability.\n"
                "5. Public Internet routing records confirm Kraft operates 3xK Tech GmbH from Germany.\n\n"
                "This means the MEGA credential stuffing attack was conducted through infrastructure "
                "operated by the single largest source of DDoS attacks on the Internet, run by an individual "
                "who monetizes a 2-million-device botnet through residential proxy services."
            ),
            "source_article": "KrebsOnSecurity - 'Who Benefited from the Aisuru and Kimwolf Botnets?' (8 Jan 2026)",
            "key_entity": "Friedrich Kraft — CEO of both 3xK Tech GmbH and Plainproxies",
            "botnet": "Kimwolf — 2 million infected Android TV boxes",
            "sdk": "ByteConnect SDK — distributed via Plainproxies, turns devices into proxy relays",
            "cloudflare_finding": "July 2025: 3xK Tech = Internet's largest source of application-layer DDoS attacks",
            "palo_alto_scanning": "November 2025: 3xK Tech IPs = ~75% of scanning for critical PAN-OS vulnerability",
        }),
        "stream": "CRIME_INTEL",
        "timestamp": NOW,
        "status": "promoted",
        "metadata_json": json.dumps({
            "signal_type": "threat_actor_intelligence",
            "source_authority": "KrebsOnSecurity",
            "severity": "CRITICAL",
            "confidence": 0.95,
        }),
        "is_priority": 1,
        "lat": None,
        "lng": None,
        "gravity": 0.95,
    },
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "hibp-zero-breach-stealer-log-hypothesis",
        "title": "HIBP shows zero breaches for compromised email — stealer logs or unlisted breach as credential source vector",
        "content": json.dumps({
            "summary": (
                "Have I Been Pwned (HIBP) returns zero breaches for the compromised email address, "
                "yet the MEGA account was demonstrably compromised via credential stuffing. "
                "This anomaly has two primary explanations:\n\n"
                "1. STEALER LOGS (most likely): Infostealer malware on a previously infected device "
                "captured the credentials. HIBP has begun indexing stealer log collections (e.g., "
                "Synthient Credential Stuffing Threat Data, October 2025) but coverage is incomplete. "
                "In 2025, infostealers pilfered 1.8 billion credentials from 5.8 million devices — "
                "an 800% surge year-over-year (DeepStrike/KELA). Lumma Stealer is the dominant variant "
                "in 2026. Stealer logs are sold for $10-100 per log on Telegram channels and Russian "
                "Market. The stolen data includes saved passwords, session cookies, and browser "
                "autofill — exactly the data needed for credential stuffing.\n\n"
                "2. UNLISTED BREACH: The credential may come from a small, unindexed breach not yet "
                "loaded into HIBP. Troy Hunt has acknowledged that thousands of smaller breaches exist "
                "that haven't been submitted or processed.\n\n"
                "3. COMBOLIST DERIVATION: Credential stuffing lists like Collection #1 (773 million emails, "
                "discovered January 2019 on MEGA itself) compile data from dozens of breaches. The original "
                "source breach may not be directly linked to this specific email in HIBP's records.\n\n"
                "The zero-breach HIBP result does NOT mean the credentials weren't leaked — it means "
                "the leak vector is likely stealer logs rather than a traditional database breach."
            ),
            "hibp_result": "0 breaches, 0 pastes",
            "infostealer_stats_2025": {
                "credentials_stolen": "1.8 billion",
                "devices_infected": "5.8 million",
                "year_over_year_change": "+800%",
                "dominant_variant": "Lumma Stealer",
                "price_per_log": "$10-100",
                "source": "DeepStrike / KELA State of Cybercrime 2026",
            },
        }),
        "stream": "CRIME_INTEL",
        "timestamp": NOW,
        "status": "promoted",
        "metadata_json": json.dumps({
            "signal_type": "analytical_assessment",
            "topic": "credential_source_vector",
            "severity": "MEDIUM",
            "confidence": 0.75,
        }),
        "is_priority": 0,
        "lat": None,
        "lng": None,
        "gravity": 0.62,
    },
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "credential-stuffing-economics-empty-accounts",
        "title": "Credential stuffing economics: why empty accounts are targeted — validated credentials sell for $5-50, aged accounts bypass spam filters",
        "content": json.dumps({
            "summary": (
                "Credential stuffing operations are economically motivated at industrial scale. "
                "The attacker's process is automated and indiscriminate:\n\n"
                "1. Acquire credential dumps (Collection #1: 773M emails; stealer logs: 1.8B credentials/year)\n"
                "2. Run automated bot against target service APIs (MEGA, Netflix, Spotify, etc.)\n"
                "3. If login succeeds → account is 'validated' regardless of content\n"
                "4. Save working account for resale or later exploitation\n\n"
                "WHY EMPTY ACCOUNTS HAVE VALUE:\n"
                "- Validated credentials sell on darkweb markets for $5-50 per account (Akamai)\n"
                "- Aged accounts bypass spam filters better than newly created ones\n"
                "- Free cloud storage (MEGA 20GB) is used for illegal content distribution\n"
                "- Compromised accounts send phishing/spam using the victim's identity\n"
                "- Bot farms use real accounts for vote/engagement manipulation\n\n"
                "The bot does not check whether the account contains files. It validates the "
                "credential and moves to the next one. This is industrial-scale automation, "
                "not targeted intelligence collection."
            ),
        }),
        "stream": "CRIME_INTEL",
        "timestamp": NOW,
        "status": "promoted",
        "metadata_json": json.dumps({
            "signal_type": "contextual_intelligence",
            "topic": "credential_stuffing_economics",
            "severity": "LOW",
        }),
        "is_priority": 0,
        "lat": None,
        "lng": None,
        "gravity": 0.35,
    },
    {
        "signal_id": str(uuid.uuid4()),
        "source": "forge_incident",
        "external_id": "as200373-full-infrastructure-profile",
        "title": "AS200373 full infrastructure profile: 92,517 IPs, 22.88% spam rate, 72,448 IPs running VPNs/proxies (Scamalytics), 56,320 IPv4 allocated",
        "content": json.dumps({
            "summary": (
                "Comprehensive infrastructure profile of ASN AS200373 (3xK Tech GmbH) compiled "
                "from multiple threat intelligence sources:\n\n"
                "CLEANTALK: 92,517 IP addresses, 46,302 detected, 10,595 spam-active (22.88% spam rate). "
                "Reports span 2020-2026, with active detections in June 2026.\n\n"
                "SCAMALYTICS: 72,448 IPs, 'almost all running anonymizing VPNs, servers, and public proxies.' "
                "Fraud score: 10/100 (low — but this is misleading given the Krebs/Cloudflare findings).\n\n"
                "IPINFO: 56,320 IPv4 addresses, 14 documented IP ranges, allocated December 2022 via RIPE. "
                "Ranked #2,385 of 42,350 networks globally, #54 in Germany. 5 upstream providers including "
                "Hurricane Electric (AS6939), RETN (AS9002), GoCodeIT (AS835).\n\n"
                "CLOUDFLARE: Named as 'the Internet's largest source of application-layer DDoS attacks' "
                "(July 2025). ~75% of scanning traffic for critical Palo Alto Networks vulnerability "
                "(November 2025).\n\n"
                "ABUSEIPDB: Multiple IPs with 46+ individual reports.\n\n"
                "FORTIGUARD: Catalogued as '3xK-3xK.Hosting.Service' in Fortinet's Internet Service Database."
            ),
            "asn": "AS200373",
            "org": "3xK Tech GmbH",
            "ipv4_count": 56320,
            "total_ips_monitored": 92517,
            "spam_active_ips": 10595,
            "spam_rate": "22.88%",
            "scamalytics_vpn_proxy_rate": "almost all",
            "allocated_date": "2022-12-20",
            "registry": "RIPE",
            "upstream_providers": ["Hurricane Electric AS6939", "RETN AS9002", "GoCodeIT AS835",
                                   "Broadband Hosting AS24785", "Rackdog AS398465"],
        }),
        "stream": "CRIME_INTEL",
        "timestamp": NOW,
        "status": "promoted",
        "metadata_json": json.dumps({
            "signal_type": "infrastructure_profile",
            "asn": "AS200373",
            "severity": "HIGH",
        }),
        "is_priority": 1,
        "lat": None,
        "lng": None,
        "gravity": 0.68,
    },
]

UPDATED_ARTICLE = '''## Incident Summary

On 19 June 2026 at 13:53:52 UTC, an unauthorized login to a personal MEGA cloud storage account was detected. The session originated from IP address **209.50.171.190** using `Go-http-client/1.1` — an automated tool. Within 25.8 minutes, two additional sessions were opened from the same infrastructure:

| Time (UTC) | IP Address | Location | Client | Duration | Status |
|---|---|---|---|---|---|
| 13:53:52 | 209.50.171.190 | Ashburn, VA | Go-http-client/1.1 | 0 seconds | KILLED |
| 14:19:38 | 209.50.190.92 | Berlin, DE | MegaApiClient/1.10.3 | 1 second | KILLED |
| 14:19:39 | 104.207.47.133 | Ashburn, VA | Go-http-client/1.1 | 233 seconds | KILLED |

## Infrastructure Attribution: 3xK Tech GmbH and the Kimwolf Botnet

All three attacker IP addresses belong to **3xK Tech GmbH** (ASN AS200373), a German hosting provider. A January 2026 KrebsOnSecurity investigation revealed that 3xK Tech is far more than a hosting company:

**Friedrich Kraft**, CEO of 3xK Tech GmbH, is simultaneously CEO of **Plainproxies** — a residential proxy service. Plainproxies distributed the **ByteConnect SDK** through devices compromised by the **Kimwolf botnet**, which infected over **2 million unofficial Android TV streaming boxes**. These infected devices were transformed into proxy relays for malicious traffic.

In **July 2025**, Cloudflare reported that 3xK Tech had become **"the Internet's largest source of application-layer DDoS attacks."** In November 2025, 3xK Tech IP addresses were responsible for approximately **75% of Internet scanning** for a critical Palo Alto Networks vulnerability.

### Infrastructure Scale

| Source | Metric |
|---|---|
| IPinfo | 56,320 IPv4 addresses across 14 ranges |
| CleanTalk | 92,517 monitored IPs, 10,595 spam-active (**22.88% spam rate**) |
| Scamalytics | 72,448 IPs, "almost all running anonymizing VPNs, servers, and public proxies" |
| Cloudflare | #1 source of application-layer DDoS attacks (July 2025) |
| FortiGuard | Catalogued as internet service database entry |

**Abuse contact:** friedrich.kraeft@3xktech.cloud / +4917641256819 / Altenhofer Weg 21, Schorfheide, 16244, Germany.

## GDPR Forensic Analysis

A MEGA GDPR data export confirmed the full scope of the breach:

- **5 total sessions**: 2 legitimate (account holder's Chrome browser + Android app, both South Africa, both alive), 3 attacker (all terminated)
- **All attacker sessions killed** by the account holder
- **No confirmed file exfiltration**: `file_related_ips` contains only the account holder's IP. Zero share links created. Last file download was March 2022.
- **Anomalous activity**: A chat entity was created at T+15min during the attack window — likely automated bot behaviour
- **MegaApiClient/1.10.3** session lasted only 1 second before termination, preventing bulk download

## Credential Source: The HIBP Zero-Breach Anomaly

Have I Been Pwned returns **zero breaches** for the compromised email address, yet the account was demonstrably compromised. This points to **stealer logs** rather than a traditional database breach as the credential source.

In 2025, infostealer malware pilfered **1.8 billion credentials** from 5.8 million devices — an 800% surge year-over-year. Lumma Stealer is the dominant variant in 2026, with subscriptions starting at $250/month and logs sold for $10-100 each on Telegram channels and Russian Market.

Stealer logs capture saved passwords, session cookies, and browser autofill data from infected devices. Unlike traditional breaches (where a service's database is compromised), stealer logs originate from malware on the user's device or a device where the credentials were entered. HIBP has begun indexing stealer log collections but coverage remains incomplete.

## Attack Vector: Credential Stuffing at Industrial Scale

This was **not a targeted attack**. Credential stuffing is automated and indiscriminate:

1. Credentials acquired from stealer logs or dumps (Collection #1: 773M emails; total market: 2.86 billion credentials circulating in 2025)
2. Automated bot tests credentials against MEGA API
3. If login succeeds, account is "validated" regardless of content
4. Working account saved for resale ($5-50) or later exploitation

Empty accounts have value: validated credentials are sold on darkweb markets, aged accounts bypass spam filters, free cloud storage is used for illegal content distribution, and compromised accounts enable phishing campaigns using the victim's identity.

The Go-http-client/1.1 user agent, the use of multiple proxy nodes from AS200373, and the 25.8-minute automated attack window are all consistent with industrial-scale credential testing — not targeted intelligence collection.

## Assessment

**Confidence: HIGH.** This was an automated credential stuffing attack conducted through 3xK Tech GmbH infrastructure — a network identified by Cloudflare as the Internet's largest source of DDoS attacks, operated by an individual who runs a botnet-powered residential proxy service. The credential source was most likely stealer logs rather than a traditional database breach.

**Outcome: CONTAINED.** All attacker sessions terminated. No file exfiltration confirmed. No share links created.

## Indicators of Compromise

| Indicator | Type | Context |
|---|---|---|
| 209.50.171.190 | IPv4 | Initial credential test (0s) |
| 104.207.47.133 | IPv4 | Reconnaissance session (233s) |
| 209.50.190.92 | IPv4 | MegaApiClient exfiltration attempt (1s) |
| 74.119.149.39 | IPv4 | SSL SAN cross-reference (shared cert) |
| AS200373 | ASN | 3xK Tech GmbH — all attacker IPs |
| Go-http-client/1.1 | User-Agent | Credential testing tool |
| MegaApiClient/1.10.3 | Client ID | MEGA SDK bulk download |
| 209.50.171.0/24 | Subnet | Primary attack subnet |
| 104.207.47.0/24 | Subnet | Secondary attack subnet |
| 209.50.190.0/24 | Subnet | Exfiltration subnet |

## Sources

- MEGA GDPR data export (sessions.json, files.json, contacts.json)
- KrebsOnSecurity — "Who Benefited from the Aisuru and Kimwolf Botnets?" (8 January 2026)
- Cloudflare — 3xK Tech DDoS report (July 2025)
- IPinfo.io, Shodan, AbuseIPDB, FraudGuard, Scamalytics, CleanTalk for AS200373
- DeepStrike — "Stealer Log Statistics 2025" (1.8B credentials, 800% surge)
- KELA — State of Cybercrime 2026 (2.86B credentials in circulation)
- Troy Hunt — "The 773 Million Record Collection #1 Data Breach"
- SecurityBrief NZ — MEGA credential stuffing incident
- OWASP — Credential Stuffing documentation
- Akamai, Imperva — credential stuffing economics
'''


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
                conn.execute("UPDATE signals SET gravity_score = ?, published_at = ? WHERE signal_id = ?",
                             (g, NOW, sig["signal_id"]))
                print(f"  [+] G {g:.2f} | {sig['title'][:80]}...")

        # Link new signals to case and actor
        ext_ids = [s["external_id"] for s in SIGNALS]
        ph = ",".join("?" * len(ext_ids))
        rows = conn.execute(
            f"SELECT signal_id FROM signals WHERE external_id IN ({ph})", ext_ids
        ).fetchall()
        for r in rows:
            conn.execute("INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)",
                         (CASE_ID, r["signal_id"], "Full investigation run"))
            conn.execute("INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                         (r["signal_id"], ACTOR_ID, "threat_infrastructure"))

        # Update actor description with Krebs findings
        conn.execute("""
            UPDATE actors SET description = ? WHERE actor_id = ?
        """, (
            "German hosting provider operating under ASN AS200373. CEO Friedrich Kraft also operates "
            "Plainproxies, a residential proxy service linked to the Kimwolf botnet (2 million infected "
            "Android TV boxes). In July 2025, Cloudflare identified 3xK Tech as 'the Internet's largest "
            "source of application-layer DDoS attacks.' In November 2025, 3xK Tech IPs were responsible "
            "for ~75% of scanning for a critical Palo Alto Networks vulnerability. Infrastructure: 56,320 "
            "IPv4 addresses, 22.88% spam rate (CleanTalk), 'almost all' IPs running VPNs/proxies "
            "(Scamalytics). Investigated by KrebsOnSecurity (January 2026). "
            "Abuse contact: friedrich.kraeft@3xktech.cloud, +4917641256819, "
            "Altenhofer Weg 21, Schorfheide, 16244, Germany.",
            ACTOR_ID,
        ))
        print(f"\n  [+] Actor #{ACTOR_ID} updated with Krebs/Kimwolf intelligence")

        # Update article
        conn.execute("UPDATE articles SET body_markdown = ?, updated_at = ? WHERE slug = ?",
                     (UPDATED_ARTICLE, NOW, ARTICLE_SLUG))
        print(f"  [+] Article updated with full investigation")

        conn.commit()

        total = conn.execute("SELECT COUNT(*) as c FROM case_signals WHERE case_id = ?", (CASE_ID,)).fetchone()
        all_sigs = conn.execute(
            "SELECT gravity_score, title FROM signals WHERE source = 'forge_incident' ORDER BY gravity_score DESC"
        ).fetchall()
        print(f"\nCase #{CASE_ID}: {total['c']} signals")
        for s in all_sigs:
            g = s["gravity_score"] or 0
            print(f"  G {g:.2f} | {s['title'][:80]}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

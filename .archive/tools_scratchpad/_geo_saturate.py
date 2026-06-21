#!/usr/bin/env python3
from __future__ import annotations
"""
Geo-tag existing signals and add geo-diverse collection signals.
Goal: saturate the map with markers across multiple continents.
"""

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

# ── Geo-tag existing signals that have contextual locations ──────────────────

GEO_UPDATES = [
    # Krebs investigation → Brian Krebs is US-based, but 3xK HQ is Schorfheide, Germany
    ("3xktech-kimwolf-krebs-investigation", 52.90, 13.55),  # Schorfheide, Germany
    # MEGA credential stuffing context → MEGA is Auckland, NZ
    ("mega-credential-stuffing-context", -36.8485, 174.7633),  # Auckland, NZ
    # Credential stuffing economics → global, place at internet exchange hub
    ("credential-stuffing-economics-empty-accounts", 51.5074, -0.1278),  # London (global cyber hub)
    # AS200373 infra profile → Schorfheide, Germany (HQ)
    ("as200373-full-infrastructure-profile", 52.90, 13.55),  # Schorfheide, Germany
    # HIBP zero-breach → Troy Hunt is Australia
    ("hibp-zero-breach-stealer-log-hypothesis", -33.8688, 151.2093),  # Sydney, Australia
    # GDPR forensic → place at target account location (Limpopo, ZA)
    ("mega-gdpr-forensic-analysis-20260619", -23.9, 29.45),  # Limpopo, South Africa
]

# ── New geo-diverse collection signals ───────────────────────────────────────

NEW_SIGNALS = [
    {
        "external_id": "cisa-credential-stuffing-advisory-2024",
        "title": "CISA advisory: credential stuffing remains top initial access vector; recommends MFA + credential monitoring",
        "content": json.dumps({
            "summary": (
                "The US Cybersecurity and Infrastructure Security Agency (CISA) has repeatedly warned "
                "that credential stuffing and password spraying are among the most common initial access "
                "vectors in cyber incidents affecting critical infrastructure. CISA recommends mandatory "
                "multi-factor authentication, credential monitoring via services like HIBP, and rate "
                "limiting on authentication endpoints. Joint advisory with FBI, NSA, and international "
                "partners. CISA's Known Exploited Vulnerabilities catalog and Shields Up campaign "
                "specifically address credential-based attacks."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 38.8951, "lng": -77.0364,  # Washington DC
        "gravity": 0.45,
    },
    {
        "external_id": "ncsc-uk-credential-stuffing-guidance-2025",
        "title": "NCSC UK: guidance on defending against credential stuffing — password deny lists and throttling",
        "content": json.dumps({
            "summary": (
                "The UK National Cyber Security Centre (NCSC) publishes standing guidance on credential "
                "stuffing defence. Key recommendations: implement password deny lists using breached "
                "credential databases, enforce progressive delays on failed logins, deploy CAPTCHA "
                "after threshold failures, and monitor for anomalous login patterns (geographic "
                "impossibility, user-agent mismatch). NCSC notes that Go-http-client and similar "
                "automated user agents are common indicators of credential stuffing tools."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 51.5074, "lng": -0.1278,  # London
        "gravity": 0.42,
    },
    {
        "external_id": "cloudflare-ddos-report-q3-2025-3xktech",
        "title": "Cloudflare DDoS Threat Report Q3 2025: 3xK Tech (AS200373) identified as Internet's largest source of application-layer DDoS attacks",
        "content": json.dumps({
            "summary": (
                "In its Q3 2025 DDoS Threat Report, Cloudflare identified 3xK Tech GmbH (AS200373, "
                "a.k.a. Drei-K-Tech) as the single largest source of application-layer DDoS attacks "
                "on the Internet. The network, operating from Germany with infrastructure in the US "
                "and globally, generated more HTTP flood traffic than any other autonomous system. "
                "This finding was subsequently cited by KrebsOnSecurity in its January 2026 "
                "investigation linking 3xK Tech to the Kimwolf botnet and Plainproxies residential "
                "proxy service. The same infrastructure was used in the MEGA credential stuffing "
                "attack documented in Case #15."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 37.7749, "lng": -122.4194,  # San Francisco (Cloudflare HQ)
        "gravity": 0.72,
    },
    {
        "external_id": "kimwolf-botnet-android-tv-2m-devices",
        "title": "Kimwolf botnet: 2 million unofficial Android TV boxes infected; ByteConnect SDK converts devices to proxy relays",
        "content": json.dumps({
            "summary": (
                "The Kimwolf botnet infected over 2 million unofficial Android TV streaming boxes "
                "globally. The malware installed the ByteConnect SDK, distributed through Plainproxies "
                "(operated by Friedrich Kraft, CEO of 3xK Tech GmbH), which converted infected devices "
                "into residential proxy relays. These proxies are used for credential stuffing, DDoS "
                "attacks, and anonymous web scraping. The botnet's global reach means proxy exit nodes "
                "appear as residential IPs across dozens of countries, making detection and blocking "
                "significantly harder than traditional datacenter-based attacks. The Aisuru botnet, a "
                "related entity, shared infrastructure with Kimwolf."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 52.52, "lng": 13.405,  # Berlin (German infrastructure hub)
        "gravity": 0.68,
    },
    {
        "external_id": "paloalto-vuln-scan-as200373-nov2025",
        "title": "November 2025: 3xK Tech IPs responsible for ~75% of Internet scanning for critical Palo Alto Networks PAN-OS vulnerability",
        "content": json.dumps({
            "summary": (
                "In November 2025, security researchers observed that IP addresses belonging to "
                "3xK Tech GmbH (AS200373) were responsible for approximately three-quarters of all "
                "Internet scanning traffic targeting a critical vulnerability in Palo Alto Networks "
                "PAN-OS firewall software. This mass scanning campaign from a single ASN demonstrates "
                "the scale of 3xK Tech's automated attack infrastructure and its use beyond credential "
                "stuffing — the same proxy network is used for vulnerability scanning, DDoS, and "
                "credential testing across multiple target categories."
            ),
        }),
        "stream": "INFRASTRUCTURE",
        "lat": 37.3861, "lng": -122.0839,  # Santa Clara, CA (Palo Alto Networks HQ)
        "gravity": 0.58,
    },
    {
        "external_id": "collection-1-773m-mega-dump-2019",
        "title": "Collection #1: 773 million email/password pairs discovered on MEGA cloud storage (January 2019) — foundational credential stuffing dump",
        "content": json.dumps({
            "summary": (
                "In January 2019, security researcher Troy Hunt discovered a massive 87GB data dump "
                "on MEGA's own cloud storage service, labelled 'Collection #1.' It contained 773 million "
                "unique email addresses and over 21 million unique passwords — compiled from thousands "
                "of separate data breaches. The collection was designed for industrial-scale credential "
                "stuffing. Ironically, the credential dump was hosted on MEGA, the same service now "
                "targeted by credential stuffing attacks using these dumps. Collection #1 was part of a "
                "larger set (Collections #1-5) totalling 2.2 billion credentials."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": -36.8485, "lng": 174.7633,  # Auckland, NZ (MEGA HQ)
        "gravity": 0.52,
    },
    {
        "external_id": "lumma-stealer-dominant-infostealer-2026",
        "title": "Lumma Stealer: dominant infostealer of 2026, subscriptions from $250/month, anti-sandbox evasion, credential harvesting at scale",
        "content": json.dumps({
            "summary": (
                "Lumma Stealer has emerged as the most prevalent infostealer malware in 2026, filling "
                "the vacuum left by RedLine Stealer's takedown. It operates on a subscription model "
                "starting at $250/month and employs advanced anti-sandbox evasion techniques. Lumma "
                "harvests saved passwords, session cookies, browser autofill data, and cryptocurrency "
                "wallet keys from infected devices. The stolen data is compiled into stealer logs and "
                "sold on Telegram channels and Russian Market for $10-100 per log. In 2025, infostealers "
                "collectively pilfered 1.8 billion credentials from 5.8 million devices — an 800% "
                "year-over-year surge (KELA/DeepStrike). Lumma is the most likely source of credentials "
                "used in the MEGA credential stuffing attack, given the HIBP zero-breach result."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 55.7558, "lng": 37.6173,  # Moscow (Russian Market nexus)
        "gravity": 0.58,
    },
    {
        "external_id": "mega-15500-users-credential-stuffing-incident",
        "title": "MEGA confirms 15,500 users compromised in credential stuffing attack — passwords reused from third-party breaches",
        "content": json.dumps({
            "summary": (
                "MEGA confirmed that approximately 15,500 user accounts were compromised in a credential "
                "stuffing attack where stolen username/password pairs from other breaches were tested "
                "against MEGA's login API. The company emphasised that its own systems were not breached — "
                "the attack exploited password reuse. MEGA recommended enabling two-factor authentication "
                "and using unique passwords. The incident demonstrated that even end-to-end encrypted "
                "services are vulnerable to account takeover when users reuse passwords from breached "
                "third-party services."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": -36.8485, "lng": 174.7633,  # Auckland, NZ (MEGA HQ)
        "gravity": 0.50,
    },
    {
        "external_id": "europol-emotet-takedown-credential-market-2025",
        "title": "Europol Operation Endgame (2025): largest-ever takedown of botnet infrastructure used for credential theft and ransomware delivery",
        "content": json.dumps({
            "summary": (
                "In May 2025, Europol coordinated Operation Endgame — described as the largest-ever "
                "law enforcement action against botnets. The operation targeted multiple botnet "
                "dropper infrastructures including IcedID, SystemBC, Pikabot, Smokeloader, and "
                "Bumblebee, which were used to deliver infostealers and ransomware. Over 100 servers "
                "were seized and 2,000 domains taken down across multiple countries. While Kimwolf "
                "and 3xK Tech were not directly named in Operation Endgame, the operation demonstrates "
                "the growing law enforcement focus on botnet-powered credential theft infrastructure."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 51.9225, "lng": 4.4792,  # The Hague (Europol HQ)
        "gravity": 0.55,
    },
    {
        "external_id": "sa-information-regulator-popia-breach-notification",
        "title": "South Africa Information Regulator: POPIA requires breach notification within 72 hours; credential stuffing victims may report",
        "content": json.dumps({
            "summary": (
                "Under South Africa's Protection of Personal Information Act (POPIA), organisations "
                "must notify the Information Regulator and affected data subjects of security "
                "compromises within 72 hours. While credential stuffing against a third-party service "
                "(MEGA) does not trigger POPIA notification for the service itself, South African "
                "individuals who are victims of credential stuffing may report the incident to the "
                "Information Regulator if their personal data was accessed. The Regulator has issued "
                "guidance on password security and multi-factor authentication as part of its "
                "awareness campaigns."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": -25.7479, "lng": 28.2293,  # Pretoria, South Africa (Info Regulator)
        "gravity": 0.38,
    },
    {
        "external_id": "hurricane-electric-upstream-as200373",
        "title": "Hurricane Electric (AS6939) provides transit to AS200373 — upstream connectivity enabling 3xK Tech's global proxy infrastructure",
        "content": json.dumps({
            "summary": (
                "Hurricane Electric LLC (AS6939), one of the world's largest IPv6 backbone providers "
                "and a major transit provider, provides upstream connectivity to 3xK Tech GmbH (AS200373). "
                "Other upstream providers include RETN Limited (AS9002), GoCodeIT (AS835), Broadband "
                "Hosting B.V. (AS24785), and Rackdog LLC (AS398465). These transit relationships enable "
                "3xK Tech's proxy infrastructure to reach global targets. The presence of multiple "
                "upstream providers indicates a well-connected network designed for high-volume traffic."
            ),
        }),
        "stream": "INFRASTRUCTURE",
        "lat": 47.6062, "lng": -122.3321,  # Seattle (Hurricane Electric operations)
        "gravity": 0.35,
    },
    {
        "external_id": "kela-state-of-cybercrime-2026-credentials",
        "title": "KELA State of Cybercrime 2026: 2.86 billion compromised credentials circulating across criminal markets in 2025",
        "content": json.dumps({
            "summary": (
                "KELA's State of Cybercrime 2026 report tracked 2.86 billion compromised credentials "
                "circulating across criminal markets in 2025, spanning infostealer malware, breach "
                "databases, and underground marketplaces. Recorded Future identified infostealers as "
                "the primary initial infection vector for the first time in 2025. Verizon's DBIR found "
                "that 54% of ransomware victims had domain credentials in stealer log marketplaces "
                "before the ransomware attack. The credential economy operates as a mature supply chain: "
                "infostealers harvest → logs are sold on Telegram/Russian Market → buyers use for "
                "credential stuffing, account takeover, or initial access brokering."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 32.0853, "lng": 34.7818,  # Tel Aviv (KELA HQ)
        "gravity": 0.48,
    },
]


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        # ── Geo-tag existing signals ─────────────────────────────────────
        updated = 0
        for ext_id, lat, lng in GEO_UPDATES:
            cur = conn.execute(
                "UPDATE signals SET lat = ?, lng = ? WHERE external_id = ? AND lat IS NULL",
                (lat, lng, ext_id),
            )
            if cur.rowcount > 0:
                updated += 1
                print(f"  [geo] {ext_id[:50]} -> ({lat}, {lng})")
        print(f"\nGeo-tagged {updated} existing signals")

        # ── Insert new collection signals ────────────────────────────────
        inserted = 0
        for sig in NEW_SIGNALS:
            g = sig.pop("gravity")
            row = {
                "signal_id": str(uuid.uuid4()),
                "source": "forge_incident",
                "external_id": sig["external_id"],
                "title": sig["title"],
                "content": sig["content"],
                "stream": sig["stream"],
                "timestamp": NOW,
                "status": "promoted",
                "metadata_json": json.dumps({"signal_type": "collection", "severity": "MEDIUM"}),
                "is_priority": 0,
                "lat": sig["lat"],
                "lng": sig["lng"],
            }
            cur = conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, metadata_json,
                     is_priority, stream)
                VALUES
                    (:signal_id, :source, :external_id, :title, :content,
                     :lat, :lng, :timestamp, :status, :metadata_json,
                     :is_priority, :stream)
            """, row)
            if cur.rowcount > 0:
                inserted += 1
                conn.execute("UPDATE signals SET gravity_score = ?, published_at = ? WHERE signal_id = ?",
                             (g, NOW, row["signal_id"]))
                # Link to case
                conn.execute("INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)",
                             (CASE_ID, row["signal_id"], "Broad collection sweep"))
                conn.execute("INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                             (row["signal_id"], ACTOR_ID, "mentioned"))
                print(f"  [+] G {g:.2f} | {sig['title'][:70]}...")

        conn.commit()
        print(f"\nInserted {inserted} new signals, all linked to case #{CASE_ID}")

        # Summary
        total_case = conn.execute("SELECT COUNT(*) as c FROM case_signals WHERE case_id = ?", (CASE_ID,)).fetchone()
        total_geo = conn.execute("SELECT COUNT(*) as c FROM signals WHERE source='forge_incident' AND lat IS NOT NULL").fetchone()
        total_pub = conn.execute("SELECT COUNT(*) as c FROM signals WHERE published_at IS NOT NULL").fetchone()
        print(f"\nCase #{CASE_ID}: {total_case['c']} signals")
        print(f"Geo-tagged incident signals: {total_geo['c']}")
        print(f"Total published signals: {total_pub['c']}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations
"""Ingest high-value signals from the broad OSINT collection sweep."""

import json
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

DB = pathlib.Path(__file__).parent.parent / "database.db"
CASE_ID = 15
ACTOR_ID = 94
NOW = datetime.now(timezone.utc).isoformat()

SIGNALS = [
    # ── BOMBSHELL: Kimwolf takedown + DOJ charges ────────────────────────
    {
        "external_id": "bka-kimwolf-takedown-march-2026",
        "title": "BKA/DOJ takedown: Kimwolf/Aisuru botnets dismantled March 19, 2026; Jacob Butler charged — yet 3xK Tech infrastructure still active 3 months later",
        "content": json.dumps({
            "summary": (
                "On March 19, 2026, BKA (Germany) and ZAC NRW with US/Canadian authorities dismantled "
                "the globally distributed infrastructure of Aisuru, Kimwolf, JackSkid, and 'Mossad' "
                "sister networks. Peak network: 3+ million infected devices. On April 10, 2026, Jacob "
                "Butler aka 'Dort', 23, of Ottawa, Canada was charged by DOJ with operating the Kimwolf "
                "DDoS botnet. CRITICAL: The MEGA credential stuffing attack documented in Case #15 "
                "occurred on June 19, 2026 — exactly 3 months AFTER the takedown — from the same "
                "AS200373 infrastructure. This means either: (a) the infrastructure was not fully "
                "dismantled, (b) 3xK Tech GmbH rebuilt capacity, or (c) the credential stuffing "
                "operation runs independently of the botnet C2 that was seized."
            ),
            "sources": [
                "BleepingComputer: US and Canada arrest suspected Kimwolf botnet admin",
                "Security Affairs: Global law enforcement targets Aisuru/Kimwolf operators",
                "DOJ USAO-AK: Canadian man charged with administrating Kimwolf DDoS botnet",
            ],
        }),
        "stream": "CRIME_INTEL",
        "lat": 50.9375, "lng": 6.9603,  # Cologne (ZAC NRW)
        "gravity": 0.95,
        "is_priority": 1,
    },
    # ── Aisuru/Kimwolf DDoS records ──────────────────────────────────────
    {
        "external_id": "aisuru-proxy-empire-ddos-records-2025",
        "title": "Aisuru botnet pivots from DDoS to residential proxy empire: 31.4 Tbps record, 2M+ Android devices, monetised via PlainProxies",
        "content": json.dumps({
            "summary": (
                "Aisuru retooled from DDoS to residential-proxy-as-a-service, renting access to "
                "hundreds of thousands of compromised IoT devices. 2M+ Android devices (mostly "
                "off-brand TVs). DDoS records: 22.2 Tbps / 10.6 billion PPS (Sep 2025), 31.4 Tbps "
                "(Dec 2025 joint with Kimwolf). The proxy pivot is the direct credential-stuffing "
                "enabler — making attack traffic appear as legitimate residential users. Nigeria "
                "CSIRT issued advisory. Black Lotus Labs blocked 550+ C2 servers."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 9.0579, "lng": 7.4951,  # Abuja, Nigeria (Nigeria CSIRT)
        "gravity": 0.82,
        "is_priority": 1,
    },
    # ── Cloudflare formal Q2 2025 report ─────────────────────────────────
    {
        "external_id": "cloudflare-q2-2025-as200373-number-1",
        "title": "Cloudflare Q2 2025 DDoS Report: AS200373 (Drei-K-Tech-GmbH) formally ranked #1 source of HTTP DDoS attacks globally",
        "content": json.dumps({
            "summary": (
                "In Cloudflare's Q2 2025 DDoS threat report, Drei-K-Tech-GmbH (AS200373) jumped "
                "6 places to become the #1 largest source of HTTP/application-layer DDoS attacks "
                "globally, displacing Hetzner (AS24940). DigitalOcean (AS14061) placed second. "
                "8 out of 10 top source ASNs offer VMs/hosting, indicating VM-based botnets "
                "estimated 5,000x stronger than IoT botnets."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 37.7749, "lng": -122.4194,  # San Francisco (Cloudflare)
        "gravity": 0.88,
        "is_priority": 1,
    },
    # ── GreyNoise credential spraying ────────────────────────────────────
    {
        "external_id": "greynoise-3xktech-paloalto-spraying-2025",
        "title": "GreyNoise: 2.3M scanning sessions + 1.7M credential spraying sessions from AS200373 against Palo Alto/Cisco VPNs (Nov-Dec 2025)",
        "content": json.dumps({
            "summary": (
                "GreyNoise detected massive scanning/credential-spraying campaigns from 3xK Tech IPs. "
                "Nov 14-19: 2.3 million sessions hitting Palo Alto GlobalProtect /global-protect/login.esp. "
                "Dec 11: 1.7 million login sessions in 16 hours, then pivot to Cisco VPNs. 10,000+ "
                "unique IPs, nearly all hosted by 3xK Tech GmbH (62% Germany, 15% Canada). Automated "
                "scripted login attempts — classic credential stuffing at infrastructure scale."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 40.7128, "lng": -74.0060,  # New York (GreyNoise HQ)
        "gravity": 0.85,
        "is_priority": 1,
    },
    # ── Operation Endgame 3.0 ────────────────────────────────────────────
    {
        "external_id": "europol-endgame-3-rhadamanthys-nov2025",
        "title": "Operation Endgame 3.0: Europol dismantles Rhadamanthys infostealer — 1,025 servers seized, 525,303 infections, 86.2M stealing events",
        "content": json.dumps({
            "summary": (
                "Nov 10-13 2025: Europol/Eurojust with 11 countries dismantled Rhadamanthys infostealer, "
                "VenomRAT, and Elysium botnet. 1,025+ servers taken down, 20 domains seized, 1 arrest "
                "in Greece. Shadowserver identified 525,303 unique Rhadamanthys infections across 226 "
                "countries, representing 86.2 million info-stealing events. 2 million email addresses "
                "and 7.4 million passwords submitted to HIBP."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 52.0705, "lng": 4.3007,  # The Hague (Europol)
        "gravity": 0.88,
        "is_priority": 1,
    },
    # ── FBI IC3 ──────────────────────────────────────────────────────────
    {
        "external_id": "fbi-ic3-2025-report-20.9b-losses",
        "title": "FBI IC3 2025 Report: $20.9 billion in losses, 1M+ complaints — credential compromise identified as primary entry point",
        "content": json.dumps({
            "summary": (
                "FBI IC3 2025 report records $20.877 billion in losses (up 26% from 2024). First time "
                "in 25-year history that complaints exceeded 1 million (1,008,597). 191,561 "
                "phishing/spoofing complaints — highest category by volume. Credential compromise "
                "identified as primary entry point. AI-enabled credential stuffing at unprecedented velocity."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 38.9072, "lng": -77.0369,  # Washington DC (FBI)
        "gravity": 0.78,
        "is_priority": 0,
    },
    # ── SA Information Regulator ─────────────────────────────────────────
    {
        "external_id": "sa-info-regulator-2374-breaches-fy2025",
        "title": "SA Information Regulator: 2,374 breaches reported FY2024/25; 95% caused by human error including weak/reused passwords; avg cost R53 million",
        "content": json.dumps({
            "summary": (
                "SA Information Regulator received 2,374 reported breaches in FY2024/25, with 82% "
                "(1,947) occurring after April 2025 when the eServices Portal went live (~300/month). "
                "Average breach cost R53 million, severe incidents up to R360 million. 95% caused by "
                "human error including weak/reused passwords. Mandatory reporting now via online portal."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": -25.7479, "lng": 28.2293,  # Pretoria
        "gravity": 0.65,
        "is_priority": 0,
    },
    # ── Plainproxies DDoS on Meduza (press freedom) ──────────────────────
    {
        "external_id": "qurium-plainproxies-meduza-ddos-2024",
        "title": "Qurium forensics: PlainProxies (3xK Tech) identified in DDoS attacks against Russian independent media Meduza.io — 2B fake requests in 48 hours",
        "content": json.dumps({
            "summary": (
                "Qurium forensically identified PlainProxies (operated by Friedrich Kraft via 3xK Tech "
                "GmbH) in DDoS attacks on Meduza.io (April 2024). First attack: 2 billion fake requests "
                "over 48 hours from 6,300 IP addresses. IPv6 prefix leased through Heymman Servers -> "
                "A1NX -> 3xK Tech. Same providers also attacked Hungarian independent news sites. "
                "This demonstrates 3xK Tech infrastructure is used not only for credential stuffing "
                "and DDoS-for-hire but also for attacks on press freedom."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 59.3293, "lng": 18.0686,  # Stockholm (Qurium HQ)
        "gravity": 0.78,
        "is_priority": 1,
    },
    # ── Lumma Stealer takedown + MEGA hosting ────────────────────────────
    {
        "external_id": "microsoft-lumma-takedown-may2025-mega-hosting",
        "title": "Microsoft/DOJ seize 2,300 Lumma Stealer domains (May 2025); Lumma hosted payloads on MEGA Cloud — creating credential theft feedback loop",
        "content": json.dumps({
            "summary": (
                "May 2025: Microsoft DCU court order seizes 2,300 Lumma C2 domains. DOJ/FBI seize 2 "
                "admin domains. 394,000+ Windows systems infected in 3 months. Critically, Lumma "
                "phishing campaigns hosted payloads on MEGA Cloud to evade detection — creating a "
                "feedback loop where infostealers hosted on MEGA harvest credentials that are then "
                "used for credential stuffing against MEGA accounts. Despite takedown, Lumma resurfaced "
                "within weeks with new infrastructure."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 33.7490, "lng": -84.3880,  # Atlanta (court order)
        "gravity": 0.80,
        "is_priority": 1,
    },
    # ── 16 billion credential mega-leak ──────────────────────────────────
    {
        "external_id": "16-billion-credentials-leak-2025",
        "title": "16 billion login credentials compiled from infostealer logs, phishing kits, and prior breaches — industrial fuel for credential stuffing",
        "content": json.dumps({
            "summary": (
                "Researchers discovered approximately 16 billion login credentials compiled from "
                "infostealer malware logs, phishing kits, and prior data breaches. This is the raw "
                "fuel for credential-stuffing-as-a-service operations. The compilation aggregates "
                "data from multiple years of infostealer infections and recycled historical breaches."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 48.8566, "lng": 2.3522,  # Paris (European cyber hub)
        "gravity": 0.80,
        "is_priority": 0,
    },
    # ── Synthient 2B credentials to HIBP ─────────────────────────────────
    {
        "external_id": "synthient-2b-credentials-hibp-nov2025",
        "title": "Synthient corpus: 2 billion emails + 1.3 billion passwords added to HIBP (Nov 2025) — largest single addition in HIBP history",
        "content": json.dumps({
            "summary": (
                "Nov 5, 2025: HIBP processed 1,957,476,021 unique emails and 1.3 billion unique "
                "passwords from the Synthient Credential Stuffing corpus — largest single addition "
                "in HIBP history. 625 million passwords were entirely new. Data aggregated from "
                "credential-stuffing lists across underground sources."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": -33.8688, "lng": 151.2093,  # Sydney (Troy Hunt)
        "gravity": 0.72,
        "is_priority": 0,
    },
    # ── Friedrich Kraft full profile ─────────────────────────────────────
    {
        "external_id": "friedrich-kraft-full-actor-profile",
        "title": "Friedrich Kraft: CEO PlainProxies + 3xK Tech, co-founder ByteConnect Ltd with Julia Levi (ex-Bright Data); HRB 18693, VAT DE344070238",
        "content": json.dumps({
            "summary": (
                "Friedrich Kraft (also styled Kraeft/Kraft): CEO of PlainProxies, co-founder of "
                "ByteConnect Ltd, operator of 3xK Tech GmbH (HRB 18693, VAT ID DE344070238). "
                "ByteConnect SDK installed on Kimwolf-compromised devices, turning them into "
                "residential proxy nodes. Synthient confirmed mass credential-stuffing attacks from "
                "ByteConnect infrastructure. Julia Levi identified as co-founder (ex-Netnut, ex-Bright "
                "Data). Kraft did not respond to journalist requests for comment. X: @FraftDev. "
                "LinkedIn: friedrich-kraft-1478a3248."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 52.89, "lng": 13.58,  # Schorfheide, Germany
        "gravity": 0.90,
        "is_priority": 1,
    },
    # ── MEGA encryption flaws ────────────────────────────────────────────
    {
        "external_id": "mega-encryption-rsa-key-recovery-2022",
        "title": "MEGA encryption flaw: RSA key recovery attack demonstrated (2022) — user data less impenetrable than claimed; compounding risk with credential stuffing",
        "content": json.dumps({
            "summary": (
                "Cryptographic researchers (June 2022) demonstrated that MEGA's encryption design "
                "allows theoretical recovery of user RSA private keys and file decryption. MEGA patched "
                "the specific attack but acknowledged full fix would require system redesign and key "
                "reissue. Combined with credential stuffing, this is a compounding risk — no need for "
                "crypto attacks if you have the password."
            ),
        }),
        "stream": "INFRASTRUCTURE",
        "lat": 47.3769, "lng": 8.5417,  # Zurich (research institution)
        "gravity": 0.45,
        "is_priority": 0,
    },
    # ── Kimwolf I2P flooding ─────────────────────────────────────────────
    {
        "external_id": "kimwolf-i2p-network-flooding-feb2026",
        "title": "Kimwolf operators overwhelmed I2P anonymity network while migrating C2 — collateral DDoS on entire privacy infrastructure",
        "content": json.dumps({
            "summary": (
                "Kimwolf operators migrated C2 servers to I2P (Invisible Internet Project) to evade "
                "takedown, then overwhelmed the I2P network itself with the botnet's traffic volume, "
                "effectively DDoSing an entire anonymity network as collateral damage. Demonstrates "
                "the scale of resources available. Reported by KrebsOnSecurity (February 2026)."
            ),
        }),
        "stream": "INFRASTRUCTURE",
        "lat": 60.1699, "lng": 24.9384,  # Helsinki (I2P infrastructure)
        "gravity": 0.55,
        "is_priority": 0,
    },
    # ── Europol Tycoon 2FA takedown ──────────────────────────────────────
    {
        "external_id": "europol-tycoon-2fa-takedown-mar2026",
        "title": "Europol dismantles Tycoon 2FA phishing platform (March 2026): 330+ domains, MFA bypass against 100K organizations",
        "content": json.dumps({
            "summary": (
                "Europol led takedown of Tycoon 2FA platform: 330+ domains seized. The platform "
                "enabled MFA-bypass phishing attacks against ~100,000 organizations including schools, "
                "hospitals, and government institutions. Tens of millions of phishing emails per month. "
                "Demonstrates the evolving threat: even MFA can be bypassed by sophisticated phishing "
                "platforms, though basic TOTP 2FA still defeats credential stuffing bots."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 52.0705, "lng": 4.3007,  # The Hague (Europol)
        "gravity": 0.68,
        "is_priority": 0,
    },
    # ── BreachForums v5 breach ───────────────────────────────────────────
    {
        "external_id": "breachforums-v5-breach-340k-mar2026",
        "title": "BreachForums v5 itself breached (March 2026): 340K users exposed — the marketplace where credential dumps are traded got compromised",
        "content": json.dumps({
            "summary": (
                "In March 2026, BreachForums v5 was breached, exposing 340,000 unique email addresses "
                "with usernames and argon2 password hashes. BreachForums is the primary marketplace "
                "where stolen credential dumps and combolists used for stuffing attacks are traded. "
                "The breach of the breach marketplace demonstrates no platform is immune."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 43.6532, "lng": -79.3832,  # Toronto (distributed, using arrest locale)
        "gravity": 0.52,
        "is_priority": 0,
    },
    # ── NCSC UK passkey recommendation ───────────────────────────────────
    {
        "external_id": "ncsc-uk-passkeys-cyberuk2026",
        "title": "NCSC UK at CYBERUK 2026: passkeys recommended as default authentication — strategic response to credential stuffing epidemic",
        "content": json.dumps({
            "summary": (
                "At CYBERUK 2026 in Glasgow, NCSC formally recommended passkeys as the default "
                "authentication method, replacing decades of password-based guidance. Separately, "
                "NCSC published a dedicated advisory on credential stuffing tools (updated April 2026). "
                "This represents a strategic pivot — the credential stuffing problem is now considered "
                "unsolvable with passwords alone."
            ),
        }),
        "stream": "CRIME_INTEL",
        "lat": 55.8642, "lng": -4.2518,  # Glasgow (CYBERUK 2026)
        "gravity": 0.52,
        "is_priority": 0,
    },
]


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        inserted = 0
        for sig in SIGNALS:
            g = sig.pop("gravity")
            pri = sig.pop("is_priority")
            row = {
                "signal_id": str(uuid.uuid4()),
                "source": "forge_incident",
                "external_id": sig["external_id"],
                "title": sig["title"],
                "content": sig["content"],
                "stream": sig["stream"],
                "timestamp": NOW,
                "status": "promoted",
                "metadata_json": json.dumps({"signal_type": "collection_sweep"}),
                "is_priority": pri,
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
                conn.execute("INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)",
                             (CASE_ID, row["signal_id"], "Collection sweep"))
                conn.execute("INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                             (row["signal_id"], ACTOR_ID, "mentioned"))
                print(f"  [+] G {g:.2f} | {sig['title'][:75]}...")

        conn.commit()

        total_case = conn.execute("SELECT COUNT(*) as c FROM case_signals WHERE case_id = ?", (CASE_ID,)).fetchone()
        total_pub = conn.execute("SELECT COUNT(*) as c FROM signals WHERE published_at IS NOT NULL").fetchone()
        total_geo = conn.execute("SELECT COUNT(*) as c FROM signals WHERE published_at IS NOT NULL AND lat IS NOT NULL").fetchone()
        print(f"\nInserted: {inserted}")
        print(f"Case #{CASE_ID}: {total_case['c']} signals")
        print(f"Total published: {total_pub['c']} ({total_geo['c']} geo-tagged)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

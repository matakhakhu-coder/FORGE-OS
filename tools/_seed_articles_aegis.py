#!/usr/bin/env python3
from __future__ import annotations
"""
Seed analyst article for Project Aegis / Regional Pathogen Surveillance case.
One article covers all 10 pinned signals — same pattern as kzn-hawks-three-signals-one-pattern.
"""

import pathlib, sqlite3, uuid
from datetime import datetime, timezone

ROOT    = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "database.db"

ARTICLE = {
    "slug":   "project-aegis-sadc-health-security",
    "title":  "Project Aegis: SADC Health Security and the Surveillance Gap",
    "summary": (
        "Open-source monitoring of disease vectors, humanitarian crises, and "
        "cross-border health risks across the SADC corridor and international transit nodes."
    ),
    "body_md": """\
## Intelligence Summary

**Project Aegis** is FORGE's multi-tier biosurveillance sensor — a passive listener on the WHO Disease Outbreak News feed, the CDC Health Alert Network (HAN), and ProMED's early-warning noise layer. Its mandate is narrow and deliberate: detect anomalous health signals in the SADC corridor and along South Africa's international transit and migration routes before they crystallise into reportable domestic events.

This brief covers the current sensor sweep across ten signals collected between April and June 2026.

---

## DRC: Food Violence and Health Infrastructure Collapse

The Democratic Republic of Congo remains the highest-risk vector for cross-border health deterioration affecting the SADC corridor. A CDC HAN advisory from April 2026 documents the intersection of organised food-related violence and health service disruption in eastern DRC — a pattern that has historically preceded cholera and mpox surges in neighbouring Zambia, Tanzania, and Zimbabwe.

**Analyst note:** Eastern DRC's health infrastructure operates below minimum WHO functional thresholds. Any significant armed disruption to the cold chain (vaccine storage) or to MSF/ICRC clinic access creates a 6–12 week lag before disease vectors cross into SADC territory. The current signal is a pre-warning, not an active outbreak.

---

## South Sudan: Hospital Infrastructure Under Attack

The Old Fangak hospital bombing (documented in a May 2026 CDC HAN advisory) is the most operationally significant single signal in this sweep. South Sudan is a major irregular migration source country for South Africa's northern border crossings (Beit Bridge, Kazungula). Individuals transiting through compromised health zones arrive in South Africa with zero documented health screening.

The bombing destroyed the last functional hospital in Jonglei State. Health authority accountability is nil. This is not a disease outbreak signal — it is an infrastructure collapse signal that removes the only early-detection mechanism from a high-transit zone.

---

## Central Africa Humanitarian Corridor

A ProMED/UNFPA situation report from the Central African Republic documents ongoing crisis conditions that place it in the same analytical bracket as eastern DRC. CAR shares a migration corridor with Chad, Sudan, and South Sudan — all of which feed into East Africa and eventually the SADC land migration network.

The FGM thematic report and the "Harmful Practices Affecting Children in Africa" advisory are not disease signals in the clinical sense but represent the same underlying indicator: health governance failure across a contiguous geographic zone.

---

## Lebanon and Ukraine: Transit Node Risk

Two signals fall outside the Africa/SADC geographic primary zone but are included in the Aegis sweep because South Africa maintains significant Lebanese-diaspora and Ukrainian communities, and direct air corridors to both conflict zones remain active through Johannesburg OR Tambo International.

The Lebanon Flash Update #20 documents active hostility escalation; the Ukraine Health Cluster Bulletin #3 covers a degraded primary healthcare system. Neither constitutes an imminent import risk, but both warrant passive monitoring through the OR Tambo public health checkpoint layer.

---

## Haiti and Broader Latin America

ProMED's Latin America and Caribbean weekly situation report is included in this sweep as a baseline calibration signal. Haiti's recurring cholera and gang-related health disruption patterns are geographically distant but methodologically useful — the WHO response timeline in Haiti has historically predicted response latency in comparable sub-Saharan African events by approximately 8–14 weeks.

---

## Assessment

The current Aegis sweep does not identify an active or imminent outbreak risk for South Africa. What it does identify is a **degraded-infrastructure belt** running from eastern DRC through South Sudan and Central Africa — the precise corridor that feeds irregular migration pressure onto South Africa's northern border crossings.

The surveillance gap is not in signal collection; it is in the linkage between border health screening data (held by the Department of Health) and open-source health intelligence. FORGE's Aegis sensor is a passive compensating control for that gap.

**CoE note:** The 10 signals in this case carry gravity scores between 0.424 and 0.495 — reflecting CDC HAN and ProMED source authority — but the case CoE is constrained by the absence of a direct South African domestic health event signal. The true risk weight is higher than the automated score suggests.

---

*Project Aegis sensor data is collected via WHO Disease Outbreak News RSS, CDC Health Alert Network RSS, and ProMED Mail RSS. All sources are public open-source. No classified or restricted health data is used or implied.*
""",
    "stream":       "PRIORITY",
    "author":       "ZA-DIVERGENT Analyst",
    "status":       "published",
    "published_at": "2026-06-08T14:00:00+00:00",
    "tags":         '["health-security", "sadc", "disease-surveillance", "project-aegis"]',
}

# All 10 case 11 signal IDs that this article covers
SIGNAL_IDS = [
    "34a1be1a-1e5e-41c3-a61a-c3b9023324fa",  # Gaza agriculture
    "811016d3-9abd-457e-8f50-2bd3a0aeed65",  # DRC food violence
    "83fb987d-2ede-4f28-bfb8-2d254d4bcfd2",  # Lebanon flash update
    "8db51668-0502-42de-8bff-dbdb4464169a",  # Gaza infection risk
    "f511c9c6-03c5-4793-8e90-71dafcea75fa",  # South Sudan hospital
    "d8e5a1db-87bf-4ec8-bed0-ba0afe56732c",  # Haiti/LatAm
    "85500d07-5284-48fe-93e9-c83015fd75ad",  # Ukraine health
    "9dee7b1c-6611-4128-bc98-8a03e579afc8",  # FGM annual report
    "aad3e8e2-87cb-4b63-a576-d3611fa8d0b6",  # Harmful practices
    "2a3a8fee-72a9-4751-8d2a-f9aaa3d25f58",  # UNFPA Central Africa
]


def run() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        existing = cur.execute(
            "SELECT slug FROM articles WHERE slug = ?", (ARTICLE["slug"],)
        ).fetchone()

        if existing:
            print(f"[aegis-article] already exists: {ARTICLE['slug']}")
        else:
            now = datetime.now(timezone.utc).isoformat()
            cur.execute("""
                INSERT INTO articles
                    (title, slug, summary, body_markdown,
                     stream, author, status, published_at,
                     created_at, updated_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ARTICLE["title"], ARTICLE["slug"], ARTICLE["summary"],
                ARTICLE["body_md"],
                ARTICLE["stream"], ARTICLE["author"], ARTICLE["status"],
                ARTICLE["published_at"], now, now, ARTICLE["tags"],
            ))
            print(f"[aegis-article] inserted: {ARTICLE['slug']}")

        conn.commit()
        print(f"[aegis-article] covers {len(SIGNAL_IDS)} signals")
        print(f"[aegis-article] add these to SIGNAL_ARTICLE_MAP in tools/publish.py:")
        for sid in SIGNAL_IDS:
            print(f'    "{sid}": "{ARTICLE["slug"]}",')

    finally:
        conn.close()


if __name__ == "__main__":
    run()

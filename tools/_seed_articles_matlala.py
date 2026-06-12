#!/usr/bin/env python3
from __future__ import annotations
"""Seed the Operation Matlala analyst article and publish its lead signal.

Case 12 (Operation: SAPS Tender Capture -- Cat Matlala R360m Contract) was
opened during the 2026-06-12 analyst session. This script seeds the analyst
article covering that case and marks its lead signal as published so it
surfaces on the ZA-DIVERGENT timeline.
"""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).parent.parent / "database.db"

LEAD_SIGNAL_ID = "abfd0ffb-e1d3-4e99-be3d-420676ebb923"

ARTICLE = {
    "title": "Operation Matlala: How a R360m Tender Became a National Police Crisis",
    "slug": "operation-matlala-saps-tender-capture",
    "summary": (
        "An irregular R360m SAPS medical tender awarded to Pretoria businessman "
        "Vusimuzi 'Cat' Matlala has, over eleven months, escalated from a vetting "
        "failure into the suspension of the National Police Commissioner, the "
        "arrest of twelve senior officers, and testimony at the Madlanga "
        "Commission implicating the SAPS Organised Crime unit."
    ),
    "stream": "CRIME_INTEL",
    "author": "ZA-DIVERGENT Analyst",
    "tags": json.dumps([
        "cat matlala", "saps", "masemola", "madlanga commission",
        "tender fraud", "police corruption", "shibiri",
    ]),
    "body_markdown": """\
On 5 June 2026, News24 reported that nine SAPS officers had been suspended over
Vusimuzi "Cat" Matlala's controversial R360m police tender — the latest
escalation in a story FORGE's signal corpus has been tracking since July 2025.
The pipeline scored this signal at **0.45**, the highest gravity recorded for
any South African signal in the current ingest cycle. It is not an isolated
event. It is the newest layer of a case that now reaches the apex of SAPS
leadership.

## The Tender

Matlala, a Pretoria businessman known as the "Tender King," secured a R360m
SAPS contract (reported elsewhere as Medicare24, and at one point as R228m
and R650m depending on the accounting scope) without proper vetting. SAPS's
own CFO has since described the contract as an "embarrassment," and internal
red flags reportedly showed the bid "should have been disallowed."

## The Escalation Timeline

| Date | Development |
|---|---|
| Jul 2025 | Matlala linked to Police Minister Senzo Mchunu in a protection-bribe scandal |
| Aug 2025 | Reports surface that Mchunu phoned Matlala ten times before his arrest |
| Oct 2025 | SAPS internal inquiry ties Matlala to **80 separate corruption cases** |
| Nov 2025 | Deputy Minister alleges a phone-bugging attempt linked to the tender |
| Mar 2026 | **Twelve senior officers ("the dirty dozen") arrested**; National Commissioner Fannie Masemola served with a warrant |
| Apr 2026 | President Ramaphosa **suspends Masemola** pending trial outcome |
| Jun 2026 | Madlanga Commission hears Organised Crime boss **Richard Shibiri had an alleged interest** in the tender; Shibiri dismissed; nine more officers suspended |

## Why This Is a Capture Case, Not a Procurement Case

A single irregular tender is a procurement story. A tender that produces the
suspension of a sitting National Police Commissioner, the arrest of twelve
senior officers, and testimony reaching the unit responsible for organised
crime investigations is something else: evidence of a network that used a
procurement relationship to place or protect personnel across SAPS command
structure.

Daily Maverick's framing is direct — Matlala's "plans for political control
followed the State Capture playbook." FORGE's corpus supports that framing.
The actors implicated span operational (arrested officers), command
(Masemola), and oversight (Shibiri, Organised Crime) layers of the same
institution.

## The KZN HAWKS Bridge

Richard Shibiri's dismissal is not contained to this case. The same Madlanga
Commission session that surfaced his alleged interest in the Matlala tender
also produced the testimony driving Case 10 (*KZN HAWKS Institutional
Integrity*) — the unravelling of the 541kg cocaine theft and R200m missing
narcotics case. Feroz Khan, named in both threads, is the connective actor.
This is not two cases that happen to share a commission. It is one
commission's testimony illuminating two faces of the same institutional
failure.

## Analyst Assessment

FORGE has opened **Case 12: Operation: SAPS Tender Capture — Cat Matlala
R360m Contract**, pinning 15 signals spanning July 2025 to June 2026 with a
mean gravity of **0.27** and a peak of **0.45**. Five new actors have been
added to the registry: Matlala, Masemola, Shibiri, Brown Mogotsi, and Feroz
Khan. Relationships have been wired connecting Matlala to SAPS and the NPA,
Masemola to SAPS and his suspension by Ramaphosa, and Shibiri/Khan to the
Case 10 HAWKS thread.

Brown Mogotsi is a bridge actor worth separate attention: the same individual
who "revealed [a] probe into Cat Matlala's police tender" in February 2026
was, in June 2026, denied bail on charges relating to an alleged staged
assassination attempt connected to the Phala Phala saga. Whether this
indicates one actor operating across two unrelated scandals, or a deeper
connection between them, is an open question FORGE flags but does not resolve.

**Recommended collection:** Madlanga Commission daily transcripts (Shibiri
and Khan testimony in full); SAPS procurement audit on Medicare24-linked
contracts; CIPC search on Matlala's private security company registration;
continued monitoring of Mogotsi's court proceedings for cross-references to
both the Matlala and Phala Phala matters.

---
*All assessments are provisional and based on open-source signals.*
""",
}


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO articles
                (title, slug, summary, body_markdown, stream, author,
                 status, published_at, tags)
            VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?)
        """, (
            ARTICLE["title"], ARTICLE["slug"], ARTICLE["summary"],
            ARTICLE["body_markdown"], ARTICLE["stream"], ARTICLE["author"],
            now, ARTICLE["tags"],
        ))
        print(f"[article] {ARTICLE['slug']}")

        conn.execute("""
            UPDATE signals SET published_at = ? WHERE signal_id = ? AND published_at IS NULL
        """, (now, LEAD_SIGNAL_ID))
        print(f"[signal] published {LEAD_SIGNAL_ID}")

        conn.commit()
        print("[done]")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

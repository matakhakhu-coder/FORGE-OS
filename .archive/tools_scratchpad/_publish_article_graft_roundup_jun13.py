#!/usr/bin/env python3
from __future__ import annotations
"""One-off: insert graft-roundup article (Joshco CEO bail + SIU Home Affairs racket), publish signals."""

import sqlite3
import json
import pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "database.db"

SIGNAL_IDS = [
    "423f421e-9182-469b-b684-3d5e61a68d38",  # Joshco CEO bail
    "0450004e-732e-4e96-9044-bd5210ae9a33",  # SIU Home Affairs
]

SLUG = "graft-roundup-joshco-home-affairs-june-2026"

BODY = """Two open-source signals surfaced this cycle by FORGE's monitoring layer (gravity scores
**0.53** and **0.50**) point to active, high-value corruption investigations running on parallel
tracks — one inside a Johannesburg municipal entity, the other inside the national Department of
Home Affairs. Neither has yet been triangulated against an existing FORGE case, but both meet the
threshold for analyst attention and are recorded here as standalone watch items.

## Joshco: R2 Million in Cash, R50,000 Bail

Themba Mathibe, acting CEO of the Johannesburg Social Housing Company (Joshco) — the City of
Johannesburg's social housing delivery arm — appeared at the Alexandra Magistrate's Court on
28 January 2026 and was granted **R50,000 bail** on a charge of money laundering.

| Field | Detail |
|---|---|
| Subject | Themba Mathibe (Acting CEO, Joshco) |
| Adjudicating body | Alexandra Magistrate's Court |
| Bail | R50,000 |
| Date | 28 January 2026 |
| Cash recovered | ≈ R2 million (at residence) |
| Postponed to | 2 June 2026 |

The arrest followed an investigation into procurement irregularities at Joshco, with the SAPS
cold case unit and Special Task Force recovering approximately R2 million in cash at Mathibe's
home. The case was postponed for further investigation, with police not ruling out additional
arrests. The ANC Youth League has publicly called on Mathibe to account for the funds.

## Home Affairs: A R180 Million Permit Marketplace

Separately, the Special Investigating Unit (SIU) briefed media on the outcome of a long-running
probe into the Department of Home Affairs, describing a permit and visa "marketplace" that cost
the state an estimated **R180 million**.

| Field | Detail |
|---|---|
| Investigating body | Special Investigating Unit (SIU) |
| Estimated loss | R180 million |
| Period covered | October 2004 – February 2024 |
| Officials implicated | 4 (each ~R25,000/month salary) |
| Method | WhatsApp-brokered permit/visa approvals, spousal accounts, "Permit"/"Visa Process"/"Building Material" payment fronts |

According to the SIU, four officials — on modest salaries — allegedly ran a syndicate that sold
favourable outcomes on asylum, visa and permanent residence applications to foreign nationals for
between R500 and R3,000 per case, laundering proceeds through spouses' bank accounts and shell
companies into property and vehicles. The investigation spans a twenty-year window and Home
Affairs has since said it is acting against implicated officials.

## Analyst Assessment

Both signals describe the same structural pattern FORGE has tracked across multiple cases this
year: officials on modest civil-service salaries using spousal accounts and front companies to
launder proceeds from procurement or permit-approval capture. Neither subject nor institution
currently has a matching node in the FORGE actor graph, so no case has been opened — these are
logged as watch items pending a second corroborating signal (e.g. court date follow-through for
Mathibe on 2 June 2026, or named officials in the SIU referral to the NPA).

**Recommended collection:** Alexandra Magistrate's Court roll for the 2 June 2026 Mathibe
postponement; SIU referral list to the NPA for the Home Affairs officials named in the press
briefing; City of Johannesburg council oversight committee minutes referencing Joshco procurement.

---
*All assessments are provisional and based on open-source signals.*
"""

SUMMARY = (
    "Two parallel corruption probes surfaced this cycle: Joshco acting CEO Themba Mathibe granted "
    "R50,000 bail after R2m cash was found at his home, and the SIU's exposure of a R180m Home "
    "Affairs permit-and-visa marketplace run by officials via spousal accounts."
)

TAGS = ["corruption", "joshco", "home-affairs", "siu", "money-laundering", "watch-item"]


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO articles
               (title, slug, summary, body_markdown, stream, author, status, published_at, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "Graft Roundup: Joshco CEO's R2m Cash Stash and a R180m Home Affairs Permit Racket",
                SLUG,
                SUMMARY,
                BODY,
                "CRIME_INTEL",
                "ZA-DIVERGENT Analyst",
                "published",
                now,
                json.dumps(TAGS),
            ),
        )
        for sid in SIGNAL_IDS:
            conn.execute(
                "UPDATE signals SET published_at = ? WHERE signal_id = ?",
                (now, sid),
            )
        conn.commit()
        print(f"[ok] article inserted, signals published_at={now}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

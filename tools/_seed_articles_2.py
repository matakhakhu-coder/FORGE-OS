#!/usr/bin/env python3
from __future__ import annotations
"""Seed four additional analyst articles covering remaining published signals."""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).parent.parent / "database.db"

ARTICLES = [
    {
        "title": "The Madlanga Pattern: HAWKS and Limpopo's Municipal Fraud Network",
        "slug": "madlanga-hawks-limpopo-municipal-fraud",
        "summary": (
            "The April 2026 arrest of Julius Mkhwanazi by the SAPS Madlanga task team "
            "is not an isolated enforcement action — it is the latest node in a multi-target "
            "HAWKS operation against Limpopo's municipal procurement network that has been "
            "running since at least 2025."
        ),
        "stream": "CRIME_INTEL",
        "author": "ZA-DIVERGENT Analyst",
        "tags": json.dumps(["hawks", "limpopo", "mkhwanazi", "madlanga", "municipal fraud", "procurement"]),
        "body_markdown": """\
On 18 April 2026, Julius Mkhwanazi was arrested by the SAPS Madlanga task team on charges
of fraud and corruption. FORGE's signal records this as a TimesLIVE-sourced item with a
gravity score of **0.42** — above the pipeline threshold for priority review.

The arrest is the third enforcement action recorded in Limpopo municipal structures within
a 72-hour window that April. Two days earlier, on 16 April, four other municipal officials
were arrested in a separate but related HAWKS operation.

## The Madlanga Task Team

The Madlanga task team is a specialised unit within SAPS deployed specifically against
procurement fraud in Limpopo. Its repeated activation in April 2026 suggests either a
coordinated multi-target operation reaching an arrest phase, or the maturation of an
investigation that has been building since the **Polokwane tender probe** of September 2025 —
a case that FORGE cross-references as a likely earlier phase of the same investigation.

## The Limpopo Municipal Fraud Architecture

Limpopo municipalities have been a persistent site of procurement irregularities identified
by the Auditor-General. The pattern typically involves:

1. Inflated or fictitious supplier registration
2. Contract award to connected parties without competitive tender
3. Payment certification by officials with financial relationships to the vendor

The Mkhwanazi arrest fits this architecture. The charges — fraud and corruption — are
consistent with procurement-linked conduct rather than operational misconduct.

## FORGE Case Status

Case 7, *Limpopo Municipal Procurement Fraud — April 2026 HAWKS Operations*, currently
holds 3 pinned signals and an Evidence Weight of **0.42**. Three actors are linked:
the HAWKS unit, the municipality, and Mkhwanazi as a named subject.

The two-operation structure in April 2026 (April 16 + April 18) suggests the Madlanga
task team is working through a target list. Further arrests are analytically expected.

**Recommended collection:** Polokwane municipality tender records (CIPC company search
on vendors awarded contracts 2023–2025), SIU referral logs for Limpopo municipalities.

---
*All assessments are provisional and based on open-source signals.*
""",
    },
    {
        "title": "Emfuleni's Grief: The Murder of a Municipal Official and a Family Left Waiting",
        "slug": "emfuleni-martha-rantsofu-murder-investigation",
        "summary": (
            "Martha Rantsofu, an Emfuleni Local Municipality official, was killed under "
            "circumstances that her family describes as targeted. Nine months later, her "
            "brother has spoken publicly — not to announce an arrest, but to describe "
            "a family still waiting for answers from an investigation that has produced none."
        ),
        "stream": "CRIME_INTEL",
        "author": "ZA-DIVERGENT Analyst",
        "tags": json.dumps(["emfuleni", "rantsofu", "municipal official", "murder", "gauteng"]),
        "body_markdown": """\
Martha Rantsofu was a municipal official at Emfuleni Local Municipality in Gauteng's
Sedibeng District. She was murdered. The circumstances of her death — as reported by
Daily Maverick and captured in FORGE's signal corpus — suggest a targeted killing
rather than opportunistic crime.

FORGE holds one signal on this case. The signal's gravity score is low by pipeline
standards (**0.31**), assigned automatically based on source credibility and content
length. The true analytical weight is higher. A targeted killing of a municipal official
is categorically significant: such killings are rarely isolated, they are designed to
send a message, and they typically occur in the context of procurement disputes,
whistleblower exposure, or factional political conflict.

## What the Signal Tells Us

The Daily Maverick report on which the signal is based features Martha Rantsofu's
**brother**, not a prosecutor, not a police spokesperson. A family member speaking to
press nine months after a murder — emphasising ongoing grief, not resolution — is a
signal of investigative failure.

When families of killed officials speak publicly without news of an arrest, it is
typically because:
- The investigation has stalled
- The family believes the killing was politically motivated
- They have lost confidence in official processes

None of these scenarios is benign. All of them point to unresolved institutional risk
at Emfuleni Local Municipality.

## The Emfuleni Pattern

Emfuleni is not a municipality with a clean record. It has been repeatedly flagged by
the Auditor-General for irregular expenditure. The South African Local Government
Association (SALGA) has documented service delivery failures across Sedibeng. The
municipality has been under administration.

A murdered official, an investigation going nowhere, and a family speaking to press —
these are three data points that, together, map to a municipality with active internal
conflict over resources or information.

## FORGE Assessment

This case has not been formally elevated in FORGE's case structure. It warrants
monitoring. The signal is tagged to the Emfuleni geographic cluster. Any further signals
from Emfuleni — procurement irregularities, further staff incidents, or administration
action — should be cross-referenced against this case.

**Recommended collection:** Emfuleni AG reports 2023–2025, IPID records on any
investigation into the Rantsofu matter, Daily Maverick follow-up reporting.

---
*All assessments are provisional and based on open-source signals.*
""",
    },
    {
        "title": "The Highest Level: A Security Official's Allegations Against the Police Minister",
        "slug": "saps-police-minister-crime-syndicate-allegation",
        "summary": (
            "A senior South African security official has publicly alleged that the "
            "Minister of Police is colluding with organised crime syndicates. If the "
            "allegation has a factual basis, it would represent the most significant "
            "state capture signal in the current corpus."
        ),
        "stream": "CRIME_INTEL",
        "author": "ZA-DIVERGENT Analyst",
        "tags": json.dumps(["police minister", "saps", "state capture", "crime syndicates", "priority"]),
        "body_markdown": """\
A signal sourced from PBS and surfaced through FORGE's dork collection pipeline records
that a senior South African security official has made a direct allegation: the
**Minister of Police is colluding with organised crime syndicates**.

The signal carries a gravity score of **0.31** — assigned by the pipeline based on
content length and source classification. This score significantly undersells the
analytical importance of the allegation.

## Why This Signal Is Elevated

FORGE applies a manual gravity review protocol to signals where the subject matter
exceeds what automated scoring can capture. This signal meets that threshold for
three reasons:

**1. The source is internal.** An allegation from a senior security official is
categorically different from a civil society or media claim. A security official has
access to state intelligence, operates within classified environments, and takes on
significant personal risk when making public allegations. The PBS sourcing — while
requiring verification — suggests the allegation was made in a formal or semi-formal
context rather than anonymously.

**2. The specific claim is precise.** "Colluding with organised crime syndicates" is
not a vague political accusation. It implies knowledge of a relationship, not merely
suspicion. This level of specificity from an internal source suggests documentary or
operational intelligence behind the claim.

**3. The institutional implications are maximal.** If the Minister of Police has an
operational relationship with crime syndicates, every enforcement action by SAPS is
potentially compromised. The KZN HAWKS narcotics case, the Magaqa murder investigation
interference, the Limpopo municipal fraud pattern — all of these cases sit underneath
a command structure that may be compromised at the ministerial level.

## Analytical Status

FORGE does not attribute criminal liability. The allegation has not been confirmed by
a second source, and no SAFLII record, IPID finding, or parliamentary inquiry has
been located that corroborates it. The claim is recorded as a **single-source allegation
requiring corroboration**.

Its inclusion in the ZA-DIVERGENT corpus is justified by its potential significance.
A corroborated allegation of this kind would be the highest-gravity signal in the
current FORGE database.

**Recommended collection:** Parliamentary questions tabled to SAPS; IPID reports on
ministerial conduct; NatJOINTS and NATCOM correspondence (where accessible); further
PBS or security beat reporting.

---
*All assessments are provisional and based on open-source signals. Single-source
allegations are recorded but not confirmed.*
""",
    },
    {
        "title": "Dockets and Disappearances: How SAPS Officials Sabotage Murder Investigations",
        "slug": "saps-murder-investigation-sabotage-pattern",
        "summary": (
            "An IOL investigation has documented a systemic pattern of SAPS officials "
            "actively undermining murder investigations — missing dockets, intimidated "
            "witnesses, and deliberate procedural failures. The report provides the "
            "institutional framework for understanding why the Magaqa probe stalled "
            "and why Martha Rantsofu's family is still waiting."
        ),
        "stream": "CRIME_INTEL",
        "author": "ZA-DIVERGENT Analyst",
        "tags": json.dumps(["saps", "murder investigation", "dockets", "corruption", "institutional capture"]),
        "body_markdown": """\
IOL's investigative report — surfaced in FORGE's dork collection pipeline — details
how high-ranking SAPS officials have systematically sabotaged murder investigations.
The mechanisms documented include: **missing dockets**, **intimidated witnesses**,
and **deliberate procedural failures** that prevent cases from reaching trial-ready
status.

The gravity score assigned by the pipeline is **0.31**. As with the PBS police minister
allegation signal, this score reflects content length limitations in the source rather
than analytical importance. The report is elevated to **Priority** status for analyst
review.

## The Sabotage Mechanism

FORGE's signal corpus now contains multiple independent signals that — read together —
describe a coherent institutional sabotage architecture:

| Signal | Mechanism |
|---|---|
| KZN HAWKS cocaine theft | Evidence removed before investigation |
| KZN HAWKS R200m narcotics | Polygraph withheld from commanding officer |
| Fadiel Adams arrest | MP arrested for direct probe interference |
| IOL SAPS docket report | Systematic docket disappearance at senior level |

These are not four unrelated incidents. They describe a **layered system** in which
evidence disappears at the unit level (HAWKS), oversight is withheld at the command
level (polygraph suppression), political interference operates at the MP level
(Adams), and systemic docket management failure operates across the institution (IOL).

## The Magaqa Connection

The IOL report provides the institutional context for understanding why the Magaqa
murder probe — now nine years old — has not produced a conviction. A probe that has
generated no SAFLII judgment in nine years, in a high-profile political assassination
case, is not simply slow. It is being managed.

The Adams arrest confirms active interference. The IOL report confirms that the
infrastructure for such interference — docket management, witness access, procedural
manipulation — exists inside SAPS at a systemic level.

## Analyst Assessment

The convergence of these signals produces a picture that is analytically coherent:
South Africa's priority crime investigation capacity has been partially captured at
multiple levels simultaneously — unit, command, political, and institutional.

This is not a FORGE hypothesis. It is the logical synthesis of signals from at least
four independent sources (TimesLIVE, Daily Maverick, News24, IOL) across at least
three distinct cases (Magaqa, KZN HAWKS, Limpopo municipal). The signals corroborate
each other.

**This is the thread that ties the current corpus together.**

**Recommended collection:** IPID annual reports on docket-related misconduct;
Parliamentary monitoring on SAPS case conviction rates; Civilian Secretariat for
Police audit reports.

---
*All assessments are provisional and based on open-source signals.*
""",
    },
]


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    now = datetime.now(timezone.utc).isoformat()
    try:
        for a in ARTICLES:
            conn.execute("""
                INSERT OR IGNORE INTO articles
                    (title, slug, summary, body_markdown, stream, author,
                     status, published_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?)
            """, (
                a["title"], a["slug"], a["summary"], a["body_markdown"],
                a["stream"], a["author"], now, a["tags"],
            ))
            print(f"[article] {a['slug']}")
        conn.commit()
        print(f"\n[done] {len(ARTICLES)} articles seeded")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

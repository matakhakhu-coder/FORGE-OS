#!/usr/bin/env python3
from __future__ import annotations
"""Seed analyst articles for ZA-DIVERGENT initial publication. Run once."""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).parent.parent / "database.db"

ARTICLES = [
    {
        "title": "Operation Magaqa: Anatomy of a Cover-Up",
        "slug": "operation-magaqa-anatomy-of-a-cover-up",
        "summary": (
            "Nine years after the assassination of ANC Youth League Secretary-General "
            "Sindiso Magaqa, a pattern of deliberate obstruction has emerged — culminating "
            "in the arrest of a sitting MP for interfering with the murder probe."
        ),
        "stream": "CRIME_INTEL",
        "author": "ZA-DIVERGENT Analyst",
        "tags": json.dumps(["magaqa", "kzn", "anc", "hawks", "political assassination"]),
        "body_markdown": """\
Sindiso Magaqa was shot in Umzimkhulu, KwaZulu-Natal, on 13 July 2017. He died from his
injuries on 26 September 2017. He was the Secretary-General of the ANC Youth League Sizwe
Lathis Support Group (YLSSG) and a vocal critic of local political patronage networks in
the Harry Gwala District.

Nine years on, no one has been convicted of his murder.

## The Obstruction Pattern

FORGE's signals database contains a five-signal sequence covering the Magaqa investigation.
What emerges across those signals is not the failure of an investigation to find leads —
it is the active suppression of a probe that, multiple sources suggest, has identified
suspects.

The clearest signal in the sequence: **Fadiel Adams**, an ANC MP serving on the National
Council of Provinces (KZN seat), was arrested in May 2026 on charges of interfering with
the Magaqa murder investigation. Three independent news sources — Daily Maverick, TimesLIVE,
and EWN — confirmed the arrest, and the charges are specific to obstruction of justice rather
than incidental proximity.

Adams is not a peripheral figure. His NCC position gives him direct access to parliamentary
and executive oversight mechanisms.

## The Nine-Year Gap

A separate analytical thread flagged through FORGE is the **absence of any SAFLII judgment**
in the Magaqa matter across nine years of investigation. This is atypical for a high-profile
political assassination. The absence of court records suggests either that the case has not
reached trial-ready status — or that proceedings have been systematically delayed.

FORGE's Case 8, *Operation Magaqa: Political Interference in ANC YLSSG Murder Probe*,
currently carries 5 pinned signals and a Coefficient of Evidence of **0.28** — below the
threshold for confident attribution. The Adams arrest may raise that figure if charges
progress to prosecution.

## Provisional Assessment

The Adams arrest confirms what the signal cluster has indicated for months: the Magaqa probe
has active political interference, and that interference has now produced a criminal charge
against a sitting MP.

The case remains open. FORGE will continue monitoring SAFLII filings, NPA prosecution
updates, and any further arrests in the Adams matter.

---
*All assessments are provisional and based on open-source signals. FORGE does not attribute
criminal liability — that determination rests with the courts.*
""",
    },
    {
        "title": "KZN HAWKS: Three Signals, One Pattern",
        "slug": "kzn-hawks-three-signals-one-pattern",
        "summary": (
            "Three independent signals from the KZN Directorate for Priority Crime "
            "Investigation — a cocaine theft, a R200m drug disappearance, and commission "
            "testimony about protocol failures — converge on a single conclusion: systemic "
            "evidence-handling compromise within South Africa's elite crime unit."
        ),
        "stream": "CRIME_INTEL",
        "author": "ZA-DIVERGENT Analyst",
        "tags": json.dumps(["hawks", "kzn", "corruption", "evidence tampering", "narcotics"]),
        "body_markdown": """\
Intelligence analysis operates on the principle of convergence: a single source is a report,
two sources are a lead, three independent sources converging on the same conclusion constitute
a pattern. In the case of the KZN Hawks, FORGE holds three signals — separated by time,
source, and legal forum — that converge on a single institutional integrity failure.

## Signal 1: The Cocaine Theft (2022)

In early 2022, drug-detection canines deployed inside the KZN Hawks offices registered
positive hits for narcotics **after** a significant cocaine seizure had already been processed
through the facility. Sources cited by TimesLIVE described the disappearance as a potential
inside job. A formal investigation was opened.

No conviction has been publicly recorded.

## Signal 2: The R200 Million Drug Disappearance

At a formal commission of inquiry, testimony was heard that the KZN Hawks provincial head
*should have been subjected to a polygraph examination* after R200 million worth of narcotics
vanished from an evidence facility under his command.

The commission's recommendation implies that the standard accountability mechanism was not
applied. The signal was manually corrected to a gravity score of **0.45** after the standard
pipeline underscored it due to RSS content stripping.

## Signal 3: Protocol Failure at Commission

A third signal confirms that the commission directly examined evidence-handling protocols at
the KZN Hawks and found them wanting. The combination of the 2022 cocaine theft and the
subsequent commission findings suggests that the 2022 incident was not isolated and did not
trigger sufficient remedial action.

## Analyst Assessment

FORGE Thread A, *KZN HAWKS Institutional Integrity*, has not yet been elevated to a formal
case. The three-signal convergence meets the threshold for case opening. The pattern is:

1. Narcotics disappear from a secured HAWKS facility
2. Internal accountability mechanisms are not applied
3. A commission of inquiry confirms protocol failures

The implication that the KZN Hawks has a systematic evidence-integrity problem at the
command level is analytically supportable from open sources.

**Recommended collection:** Commission testimony transcripts, NPA correspondence on the 2022
cocaine theft investigation, and IPID records on the implicated commanding officer.

---
*All assessments are provisional and based on open-source signals.*
""",
    },
    {
        "title": "R21 Billion and a Wig Store: The Eskom Diesel Fraud",
        "slug": "eskom-r21bn-diesel-fraud-mavuso",
        "summary": (
            "AmaBhungane's investigation into a R21 billion Eskom diesel contract reveals "
            "a front company operated by a 24-year-old psychology graduate who simultaneously "
            "sold wigs online. The procurement structure raises systemic questions about "
            "state-owned enterprise due diligence."
        ),
        "stream": "INFRASTRUCTURE",
        "author": "ZA-DIVERGENT Analyst",
        "tags": json.dumps(["eskom", "procurement", "state capture", "mavuso", "diesel"]),
        "body_markdown": """\
In 2024, Eskom awarded a diesel supply contract valued at approximately **R21 billion**. The
investigation published by AmaBhungane in May 2026 reveals that a company linked to
**Minenhle Mavuso** — a 24-year-old BA Psychology graduate — was positioned as a key operator
in the contract structure.

Mavuso simultaneously ran *Milo Hair Beauty*, an online wig store.

## The Front Company Structure

AmaBhungane identifies Mavuso as operating through a registered entity used as a conduit in
the diesel procurement chain. FORGE has created an actor record for Mavuso (confidence: 0.40)
and wired a `CONTRACTED` relationship to Eskom in the entity graph.

The front company structure — a young person with no apparent sector expertise, linked to a
multi-billion rand contract — is consistent with state capture procurement architecture. In
this model, a compliant front operator holds the contract while procurement fees flow to
connected principals further up the chain.

## The Eskom Context

Eskom has been the target of sustained state capture infiltration since at least 2015. The
Zondo Commission documented procurement irregularities running into hundreds of billions of
rands. The diesel contract was awarded during a period of significant load-shedding pressure,
when emergency fuel procurement is subject to less scrutiny and accelerated processes.

Emergency procurement exemptions have historically been exploited as a vector for inflated
contracts. The R21 billion figure for diesel — a commodity with established market pricing —
raises questions about whether the contract was awarded at market rates.

## FORGE Analytical Status

The signal carries a gravity score of **0.44**, manually assigned after the standard pipeline
underscored the AmaBhungane investigation due to RSS content stripping. The true analytical
weight is estimated at **0.45–0.50** given the outlet's investigative track record and the
contract value.

**Recommended next collection:** Dork search targeting company registration records linked to
Mavuso to establish the corporate directorship network behind the contract.

---
*All assessments are provisional and based on open-source signals. FORGE does not attribute
criminal liability.*
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

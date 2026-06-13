#!/usr/bin/env python3
from __future__ import annotations
"""One-off: insert Beitbridge/Maroto article, publish signal, wire SIGNAL_ARTICLE_MAP."""

import sqlite3
import pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "database.db"
SIGNAL_ID = "f94b0c85-9fc5-49c2-8dfe-72090074f5bd"
SLUG = "beitbridge-explosives-smuggling-maroto"

BODY = """A signal flagged by FORGE's monitoring layer (gravity score **0.29**) and registered by an
analyst for direct follow-up has surfaced a sentencing outcome at the Magisterial Court Musina with
implications for the Beitbridge border corridor's contraband and explosives trafficking risk profile.

## The Interdiction

Edgar Maroto was intercepted in connection with the smuggling of explosives material through the
Beitbridge Border Post, the primary road link between South Africa and Zimbabwe and one of the
busiest land crossings on the continent. Beitbridge has long been flagged in open-source reporting
as a corridor for contraband — cigarettes, vehicles, copper cable, and increasingly higher-risk
material such as explosives precursors used in cash-in-transit and ATM bombings across Limpopo and
Gauteng.

## The Sentencing

| Field | Detail |
|---|---|
| Subject | Edgar Maroto |
| Adjudicating body | Magisterial Court Musina |
| Outcome | 20-year custodial sentence |
| Date | 10 April 2026 |
| Location | Beitbridge, Limpopo |
| Charge category | Security — explosives smuggling |

The 20-year sentence handed down by the Musina court signals that the presiding magistrate treated
the matter as a serious security offence rather than routine customs contraband — consistent with
the explosives-grade nature of the material involved.

## Analyst Assessment

This signal has been opened as **Case #13** within FORGE. The working hypothesis is that the Maroto
interdiction is not necessarily an isolated event: explosives smuggled through a single courier
typically sit downstream of a sourcing and logistics network, and the Beitbridge corridor's volume
of cross-border traffic makes it a persistent point of exposure.

Open questions the case will track:

- Whether Maroto acted as a courier for a larger network, or independently.
- Whether other interdictions at Beitbridge in the same period share sourcing, packaging, or
  transport-method signatures with this case.
- Whether any Border Management Authority (BMA) or SARS personnel are implicated as facilitators —
  a pattern seen in prior Limpopo corridor cases.

The relationship between Maroto and the Magisterial Court Musina has been recorded in the FORGE
graph as a `SENTENCED_BY` edge, and the case remains open pending further signals from the
Beitbridge corridor.

**Recommended collection:** court records and case dockets from Magisterial Court Musina for related
explosives or contraband matters in the surrounding period; BMA/SARS enforcement bulletins for
Beitbridge; press coverage of prior explosives interdictions in Limpopo for pattern comparison.

---
*All assessments are provisional and based on open-source signals.*
"""

SUMMARY = (
    "Edgar Maroto sentenced to 20 years by the Magisterial Court Musina for smuggling explosives "
    "through Beitbridge Border Post. FORGE has opened Case #13 to assess whether this is part of a "
    "wider trafficking pattern through the corridor."
)

TAGS = ["beitbridge", "explosives", "smuggling", "limpopo", "border-security", "case-13"]


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        now = datetime.now(timezone.utc).isoformat()
        import json

        conn.execute(
            """INSERT INTO articles
               (title, slug, summary, body_markdown, stream, author, status, published_at, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "Beitbridge Border Post: Edgar Maroto Sentenced in Explosives Smuggling Case",
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
        conn.execute(
            "UPDATE signals SET published_at = ? WHERE signal_id = ?",
            (now, SIGNAL_ID),
        )
        conn.commit()
        print(f"[ok] article inserted, signal {SIGNAL_ID} published_at={now}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

"""
forage/processors/relationship_extractor.py
============================================
Phase P3 — Automated Relationship Extraction

Extracts candidate entity relationships from signals by:
  1. Reading signal_actors for the current signal
  2. Filtering out noise actors (geographic locations, generic institutions)
  3. For each meaningful actor pair co-occurring in the same signal,
     scanning the signal title for known verb patterns
  4. Mapping matched verbs to relation_type
  5. Writing candidate edges to entity_relationships with:
       extraction_method = 'spacy'   (marks as auto-extracted / unverified)
       confidence = min(actor_a.conf, actor_b.conf) * VERB_WEIGHT

Analyst promotes a candidate to verified by updating extraction_method to
'manual' via /admin or direct DB edit.

Graph rendering: auto-extracted edges displayed as DOTTED lines until
promoted to 'manual'. Keeps the graph's structural signal/noise legible.

Usage (standalone retroactive pass):
    python forage/processors/relationship_extractor.py
    python forage/processors/relationship_extractor.py --limit 500 --dry-run
"""

__manifest__ = {
    "id":          "relationship_extractor",
    "name":        "Relationship Extractor",
    "description": "Auto-extracts candidate actor relationships from signal co-occurrence and verb patterns.",
    "icon":        "o",
    "entry":       "forage/processors/relationship_extractor.py",
    "args":        ["--limit", "--dry-run"],
    "job_key":     "relationship_extractor",
    "version":     "1.0.0",
}

import argparse
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("forge.relationship_extractor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = Path(__file__).resolve().parent.parent.parent / "database.db"

# ── Noise actor filter ────────────────────────────────────────────────────────
# Actors whose names match these are skipped — too generic to form edges
NOISE_NAMES = frozenset({
    "south africa", "gauteng", "pretoria", "cape town", "johannesburg",
    "kwazulu-natal", "limpopo", "mpumalanga", "north west", "free state",
    "western cape", "eastern cape", "northern cape", "south african",
    "south african municipalities",
})

def _is_noise(name: str) -> bool:
    return name.lower().strip() in NOISE_NAMES


# ── Verb pattern map ──────────────────────────────────────────────────────────
# Ordered by specificity — first match wins.
# (compiled_regex, relation_type, confidence_weight)
VERB_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"\barrested?|charged?|detained",         re.I), "INVESTIGATED_BY", 0.65),
    (re.compile(r"\braid|swoops?|search",                 re.I), "INVESTIGATED_BY", 0.55),
    (re.compile(r"\binvestigat",                          re.I), "INVESTIGATED_BY", 0.50),
    (re.compile(r"\bprosecuted?|convicted?|sentenced?",   re.I), "INVESTIGATED_BY", 0.70),
    (re.compile(r"\bleads?|heads?|commands?|appointed",   re.I), "LEADS",           0.60),
    (re.compile(r"\bceo|minister|director general",       re.I), "LEADS",           0.60),
    (re.compile(r"\bemploy|works? (?:at|for)|staff\b",    re.I), "EMPLOYED_BY",     0.50),
    (re.compile(r"\bmember|official|represent",           re.I), "AFFILIATED_WITH", 0.40),
    (re.compile(r"\bcontract|tender|procure|award",       re.I), "CONTRACTED",      0.55),
    (re.compile(r"\bpays?|funds?|finances?",              re.I), "FUNDED_BY",       0.45),
    (re.compile(r"\ballied?|coalition|partner",           re.I), "ALLIED_WITH",     0.45),
    (re.compile(r"\bopposed?|critic|challenged?",         re.I), "OPPOSED_TO",      0.45),
    (re.compile(r"\bkilled?|murder|assassinat|shot\b",    re.I), "TARGETED_BY",     0.70),
    (re.compile(r"\battacked?|threatened?",               re.I), "TARGETED_BY",     0.55),
]

def _extract_relation(text: str) -> tuple[str, float]:
    for pattern, rel_type, weight in VERB_PATTERNS:
        if pattern.search(text):
            return rel_type, weight
    return "CO_OCCURS_WITH", 0.20


# ── Core extraction for a single signal ──────────────────────────────────────

def extract_for_signal(
    signal_id: str,
    title: str,
    content: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """Extract and write candidate relationships for one signal. Returns count written."""
    rows = conn.execute(
        """SELECT sa.actor_id, a.name, a.type, a.confidence_score
           FROM signal_actors sa
           JOIN actors a ON sa.actor_id = a.actor_id
           WHERE sa.signal_id = ?
             AND a.confidence_score >= 0.3""",
        (signal_id,),
    ).fetchall()

    actors = [r for r in rows if not _is_noise(r["name"])]
    if len(actors) < 2:
        return 0

    scan_text = f"{title or ''} {(content or '')[:300]}"
    rel_type, verb_weight = _extract_relation(scan_text)

    # Skip CO_OCCURS_WITH — only write on real verb matches
    if rel_type == "CO_OCCURS_WITH":
        return 0

    now     = datetime.now(timezone.utc).isoformat()
    written = 0

    for i, a in enumerate(actors):
        for b in actors[i + 1:]:
            conf = round(
                min(a["confidence_score"], b["confidence_score"]) * verb_weight, 4
            )
            if conf < 0.15:
                continue

            # Orient INVESTIGATED_BY: enforcement actor is the object
            subj, obj = a["actor_id"], b["actor_id"]
            if rel_type == "INVESTIGATED_BY":
                kw = ("investigat", "police", "hawk", "npa", "siu", "saps")
                a_enforcer = any(k in a["name"].lower() for k in kw)
                b_enforcer = any(k in b["name"].lower() for k in kw)
                if b_enforcer and not a_enforcer:
                    subj, obj = a["actor_id"], b["actor_id"]
                elif a_enforcer and not b_enforcer:
                    subj, obj = b["actor_id"], a["actor_id"]

            existing = conn.execute(
                """SELECT 1 FROM entity_relationships
                   WHERE subject_actor_id=? AND object_actor_id=? AND relation_type=?""",
                (subj, obj, rel_type),
            ).fetchone()
            if existing:
                continue

            if not dry_run:
                conn.execute(
                    """INSERT INTO entity_relationships
                       (subject_actor_id, object_actor_id, relation_type,
                        description, confidence, extraction_method, created_at)
                       VALUES (?,?,?,?,?,'spacy',?)""",
                    (
                        subj, obj, rel_type,
                        f"Auto-extracted from signal {signal_id[:12]} | {(title or '')[:80]}",
                        conf, now,
                    ),
                )
                written += 1

    return written


# ── Public hook called from ingest_signal() ──────────────────────────────────

def extract_from_ingest(
    signal_id: str,
    title: str,
    content: str,
    conn: sqlite3.Connection,
) -> None:
    """
    Called from ingest_signal() after entity materialisation.
    Writes to the same conn — caller commits.
    Failures are silently swallowed so ingest is never blocked.
    """
    try:
        extract_for_signal(signal_id, title, content, conn, dry_run=False)
    except Exception as exc:
        _log.debug("[RelExtract] signal=%s: %s", (signal_id or "")[:12], exc)


# ── Retroactive batch pass ────────────────────────────────────────────────────

def run_retroactive(limit: int = 500, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT DISTINCT s.signal_id, s.title, s.content
           FROM signals s
           JOIN signal_actors sa ON s.signal_id = sa.signal_id
           WHERE s.gravity_score IS NOT NULL
           LIMIT ?""",
        (limit,),
    ).fetchall()

    stats = {"signals_scanned": 0, "relationships_written": 0, "dry_run": dry_run}

    for row in rows:
        stats["signals_scanned"] += 1
        written = extract_for_signal(
            row["signal_id"], row["title"], row["content"], conn, dry_run
        )
        stats["relationships_written"] += written
        if written and not dry_run:
            conn.commit()

    if not dry_run:
        conn.commit()
    conn.close()
    _log.info("Retroactive extraction complete: %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE Retroactive Relationship Extractor")
    parser.add_argument("--limit",   type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(run_retroactive(limit=args.limit, dry_run=args.dry_run))

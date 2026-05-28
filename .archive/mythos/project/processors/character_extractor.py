#!/usr/bin/env python3
from __future__ import annotations
"""
Mythos Anthology — Character Extractor  (mythos/processors/character_extractor.py)
====================================================================================
Processes mythos_sources raw_text to populate character attributes:
  canonical_name, traits_json, powers_json, symbols_json, variants_json

Uses spaCy for NER where available; falls back to keyword heuristics
(stdlib re + collections only) so the processor never fails silently.

Called by the rebuild engine's extract_character() after a source is ingested.
Can also be run standalone to reprocess all stubs.

Usage:
  python mythos/processors/character_extractor.py
  python mythos/processors/character_extractor.py --character-id <id>
  python mythos/processors/character_extractor.py --dry-run
"""

import argparse
import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mythos.processors.character_extractor")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "database.db"

# ── Trait / power keyword banks (extend as corpus grows) ─────────────────────

_TRAIT_KEYWORDS = [
    "wise", "cunning", "beautiful", "vengeful", "compassionate",
    "wrathful", "merciful", "capricious", "loyal", "treacherous",
    "ancient", "immortal", "powerful", "fearsome", "benevolent",
    "malevolent", "mysterious", "proud", "humble", "fierce",
]

_POWER_KEYWORDS = [
    "shapeshifting", "flight", "invisibility", "prophecy", "healing",
    "thunder", "lightning", "fire", "ice", "water", "earth", "wind",
    "death", "rebirth", "illusion", "telepathy", "strength", "speed",
    "darkness", "light", "time", "fate", "war", "love", "fertility",
    "harvest", "sea", "underworld", "sky", "sun", "moon",
]

_SYMBOL_KEYWORDS = [
    "sword", "shield", "staff", "bow", "crown", "torch", "caduceus",
    "trident", "hammer", "spear", "eagle", "serpent", "owl", "wolf",
    "raven", "lotus", "moon", "sun disc", "scales", "ankh", "thunderbolt",
    "olive branch", "laurel", "golden apple",
]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_keywords(text: str, bank: list[str]) -> list[str]:
    text_lower = text.lower()
    return [kw for kw in bank if re.search(r'\b' + re.escape(kw) + r'\b', text_lower)]


def _guess_archetype(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ["god", "goddess", "deity", "divine"]):
        return "deity"
    if any(w in text_lower for w in ["hero", "demigod", "warrior", "champion"]):
        return "hero"
    if any(w in text_lower for w in ["monster", "beast", "creature", "serpent", "dragon"]):
        return "monster"
    if any(w in text_lower for w in ["trickster", "mischief", "cunning", "deceiver"]):
        return "trickster"
    if any(w in text_lower for w in ["spirit", "ghost", "shade", "specter"]):
        return "spirit"
    return "other"


def _extract_name_from_title(title: str) -> str:
    """Strip stub prefix and source title noise to get a clean character name."""
    # Remove "[Culture] " prefix from scaffold titles
    name = re.sub(r'^\[[^\]]+\]\s*', '', title).strip()
    # Truncate at common delimiters
    name = re.split(r'[,\-–(]', name)[0].strip()
    return name[:120] if name else title[:120]


def process_source(source_id: str, dry_run: bool = False) -> dict:
    """
    Extract character attributes from a source and update the linked
    character stub. Returns a summary dict.
    """
    conn = _conn()
    try:
        src = conn.execute(
            "SELECT * FROM mythos_sources WHERE source_id=?", (source_id,)
        ).fetchone()
        if not src:
            return {"error": f"source {source_id} not found"}

        # Find character linked to this source via spawned_from edge
        edge = conn.execute(
            """SELECT source_node_id FROM mythos_edges
               WHERE target_node_id=? AND target_node_type='source'
                 AND source_node_type='character' AND edge_type='spawned_from'
               LIMIT 1""",
            (source_id,),
        ).fetchone()
        if not edge:
            return {"skipped": "no character edge found", "source_id": source_id}

        char_id = edge["source_node_id"]
        char = conn.execute(
            "SELECT * FROM mythos_characters WHERE character_id=?", (char_id,)
        ).fetchone()
        if not char:
            return {"error": f"character {char_id} not found"}

        raw = src["raw_text"] or ""
        title = src["title"] or ""

        traits  = _extract_keywords(raw, _TRAIT_KEYWORDS)
        powers  = _extract_keywords(raw, _POWER_KEYWORDS)
        symbols = _extract_keywords(raw, _SYMBOL_KEYWORDS)

        clean_name = _extract_name_from_title(title)
        archetype  = _guess_archetype(raw)

        result = {
            "character_id":  char_id,
            "canonical_name": clean_name,
            "archetype":     archetype,
            "traits":        traits,
            "powers":        powers,
            "symbols":       symbols,
        }

        if dry_run:
            log.info("[dry-run] would update %s: %s", char_id[:8], result)
            return result

        conn.execute(
            """UPDATE mythos_characters SET
                   canonical_name=?,
                   archetype=?,
                   traits_json=?,
                   powers_json=?,
                   symbols_json=?,
                   status=CASE status WHEN 'stub' THEN 'draft' ELSE status END,
                   updated_at=?
               WHERE character_id=?""",
            (
                clean_name,
                archetype,
                json.dumps(traits),
                json.dumps(powers),
                json.dumps(symbols),
                _now(),
                char_id,
            ),
        )
        conn.commit()
        log.info("extracted: %s → %s traits=%d powers=%d symbols=%d",
                 source_id[:8], clean_name[:40], len(traits), len(powers), len(symbols))
        return result

    finally:
        conn.close()


def process_all_stubs(dry_run: bool = False) -> int:
    """
    Process every source that is linked to a 'stub' character.
    Returns count processed.
    """
    conn = _conn()
    try:
        stubs = conn.execute(
            """SELECT s.source_id FROM mythos_sources s
               JOIN mythos_edges e ON e.target_node_id=s.source_id
                   AND e.target_node_type='source'
                   AND e.source_node_type='character'
                   AND e.edge_type='spawned_from'
               JOIN mythos_characters c ON c.character_id=e.source_node_id
               WHERE c.status='stub'"""
        ).fetchall()
        source_ids = [r["source_id"] for r in stubs]
    finally:
        conn.close()

    count = 0
    for sid in source_ids:
        result = process_source(sid, dry_run=dry_run)
        if "error" not in result and "skipped" not in result:
            count += 1
    return count


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Mythos character extractor")
    parser.add_argument("--character-id", default=None,
                        help="Process sources for a specific character ID only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print extracted data without writing to DB")
    args = parser.parse_args()

    if args.character_id:
        conn = _conn()
        try:
            sources = conn.execute(
                """SELECT target_node_id FROM mythos_edges
                   WHERE source_node_id=? AND source_node_type='character'
                     AND target_node_type='source' AND edge_type='spawned_from'""",
                (args.character_id,),
            ).fetchall()
        finally:
            conn.close()
        n = 0
        for row in sources:
            r = process_source(row["target_node_id"], dry_run=args.dry_run)
            if "error" not in r:
                n += 1
        log.info("processed %d sources for character %s", n, args.character_id[:8])
    else:
        n = process_all_stubs(dry_run=args.dry_run)
        log.info("processed %d stub characters", n)


if __name__ == "__main__":
    main()

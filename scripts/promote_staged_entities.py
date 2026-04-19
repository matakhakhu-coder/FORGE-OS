#!/usr/bin/env python
"""
scripts/promote_staged_entities.py — P3.2-06 Actor Promotion Gate
==================================================================

Promotes high-fidelity PERSON entities from signal_entities into the
actors table, making them available as linkable nodes for triple_extractor.

This closes the gap identified in Phase 3.2 where Aspirant Prosecutors
(Bradley Smith, Mosalanyane Mosala, Refilwe Motshwane, George M Maphutuma,
Theodore Leeuwschut) and other confirmed individuals existed in signal_entities
but were invisible to the relationship extraction pipeline.

Strategy
--------
1. Query signal_entities joined with signals WHERE source = 'pdf_infiltrator'
   AND label = 'PERSON' — restricts to A-tier document provenance.
2. Clean extracted text: take first line only (strips OCR newline artefacts),
   strip leading title prefixes (Mr, Dr, Adv, Miss, etc.).
3. Apply noise filter: reject patterns that are clearly not person names
   (equipment codes, document headers, abbreviations, locations, etc.).
4. Deduplicate against existing actors table (case-insensitive).
5. INSERT new actors with type='person', source_type='promoted', automated=1.

Noise filter design
-------------------
The filter uses two passes:
  a) Regex patterns — catch structural noise (OCR codes, all-caps abbreviations,
     section headers, inventory items, etc.)
  b) Explicit denylist — known non-person strings that pass the regex pass
     (generic SA legal/document terms, place names, etc.)

Usage
-----
  python scripts/promote_staged_entities.py           # live run
  python scripts/promote_staged_entities.py --dry-run # count only, no INSERT
  python scripts/promote_staged_entities.py --min-signals 2  # stricter filter
  python scripts/promote_staged_entities.py --db /path/to/db

Output
------
  Terminal: ranked list of promoted actors with signal provenance
  DB:       INSERT into actors; pipeline_runs log entry
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Config ────────────────────────────────────────────────────────────────────

# Minimum chars for a candidate name after cleaning
_MIN_NAME_LEN = 4
_MAX_NAME_LEN = 80

# Title prefixes to strip before name matching (prefix + space)
_TITLE_PREFIXES = (
    "adv. ", "adv ", "dr. ", "dr ", "mr. ", "mr ", "mrs. ", "mrs ",
    "ms. ", "ms ", "miss ", "prof. ", "prof ", "hon. ", "hon ",
    "judge ", "justice ", "commissioner ",
)

# Regex patterns indicating the text is NOT a person name
_NOISE_REGEX: list[re.Pattern] = [
    re.compile(r'^\d'),                          # starts with digit
    re.compile(r'Magnitude\s', re.I),            # earthquake magnitude
    re.compile(r'^M\d+\.\d'),                    # M2.9 shorthand
    re.compile(r'Depth:', re.I),                 # earthquake depth
    re.compile(r'FURNFIT', re.I),                # furniture inventory codes
    re.compile(r'OEQUIP', re.I),                 # equipment codes
    re.compile(r'^Bag X\d'),                     # postal bag addresses
    re.compile(r'^\w\.\s+[A-Z]'),                # section refs "E. Criteria"
    re.compile(r'\bBuilding\b', re.I),           # building names
    re.compile(r'\bBay\b', re.I),                # place names with Bay
    re.compile(r'\bMunicip', re.I),              # municipality references
    re.compile(r'^[A-Z]{2,6}$'),                 # all-caps abbreviations
    re.compile(r'^[A-Z]{2,}\s+[A-Z0-9]{2,}$'),  # ALL-CAPS compound codes
    re.compile(r'&amp;|&nbsp;', re.I),           # HTML entities
    re.compile(r'[<>{}|\\]'),                    # structural characters
    re.compile(r'\d{3,}'),                       # long numeric sequences
    re.compile(r'^\s*-[\d.]'),                   # coordinate strings "-10.287"
    re.compile(r'\.{3,}'),                       # ellipsis / truncation artefacts
]

# Explicit denylist — strings that pass the regex filter but are not person names
_DENYLIST: frozenset[str] = frozenset({
    # SA legal / document structure terms
    "schedule", "statute", "gazette", "directorate", "directors", "assented",
    "tribunal", "court", "national", "parliament", "state",
    "sccu", "afu", "adv", "nlc", "adr", "gtm", "csir",
    # Generic roles and phrases
    "owned entitie", "owned entities",
    "intelligence law", "tsunami civil",
    "district municipality", "community affair",
    "stakeholder relations", "scm governance",
    "e. criteria", "c. particular",
    "chair - operator", "desk cluster",
    "counter top", "screen desk",
    "description details",
    # Place-like false PERSON tags
    "south africa", "sa", "kwazulu", "gauteng", "mpumalanga",
    "limpopo", "vanuatu", "atka", "alaska", "myanmar",
    "charlotte amalie", "cruz bay", "alexander bay", "nelson mandela bay",
    # Signal noise
    "moneyweb", "lukhope", "eskom", "negrigent ro",
    "2 lo", "amabongwe",
})


def _resolve_db(override=None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1] / "database.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _clean_text(raw: str) -> str:
    """
    Normalise a raw signal_entities text value into a clean name string.
    - Handle OCR newline artefacts like 'Refilwe\\nMotshwane':
      * If split at newline yields a single short word (<=15 chars), try joining
        first two lines (they are a split first/last name pair).
      * Otherwise take first line only (cuts off OCR garbage like
        'George M Maphutuma\\nContributor').
    - Collapse internal whitespace
    - Strip trailing punctuation / noise
    """
    parts = [p.strip() for p in raw.split("\n") if p.strip()]
    if not parts:
        return ""

    if len(parts) >= 2 and len(parts[0]) <= 15 and " " not in parts[0]:
        # Likely a split first-name / last-name — rejoin
        text = f"{parts[0]} {parts[1]}"
    else:
        text = parts[0]

    # Collapse internal whitespace
    text = re.sub(r"\s{2,}", " ", text)
    # Strip trailing non-alphabetic noise characters
    text = re.sub(r"[^\w\s]+$", "", text).strip()
    return text


def _strip_title(text: str) -> str:
    """Strip known title prefixes from a person name."""
    lower = text.lower()
    for prefix in _TITLE_PREFIXES:
        if lower.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _is_valid_name(text: str) -> bool:
    """
    Return True if text looks like a plausible person name.

    Criteria
    --------
    - Length in [_MIN_NAME_LEN, _MAX_NAME_LEN]
    - Contains at least one internal space (first + last name minimum)
    - Not in _DENYLIST (case-insensitive)
    - Doesn't match any _NOISE_REGEX pattern
    - Has at least one lowercase letter (excludes all-caps non-name strings)
    """
    if not text:
        return False
    if len(text) < _MIN_NAME_LEN or len(text) > _MAX_NAME_LEN:
        return False
    if " " not in text:
        return False  # single-token strings are almost never full names here
    lower_text = text.lower().strip()
    if lower_text in _DENYLIST:
        return False
    # Check if first word is a document-structure term (catches "Schedule 1", "Gazette No", etc.)
    first_word = lower_text.split()[0] if lower_text.split() else ""
    _STRUCTURE_FIRST_WORDS = frozenset({
        "schedule", "statute", "gazette", "annex", "annexure", "appendix",
        "section", "clause", "chapter", "part", "exhibit", "regulation",
        "regulation", "proclamation", "notice",
    })
    if first_word in _STRUCTURE_FIRST_WORDS:
        return False
    for pattern in _NOISE_REGEX:
        if pattern.search(text):
            return False
    # Must contain at least one lowercase letter (all-caps = abbreviation/header)
    if not any(c.islower() for c in text):
        return False
    return True


def run(
    db_path: Path,
    min_signals: int = 1,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _connect(db_path)

    # ── Step 1: load candidate PERSON entities from A-tier sources ───────────
    rows = conn.execute("""
        SELECT  se.text,
                COUNT(DISTINCT se.signal_id) AS sig_count,
                SUM(se.count)                AS total_mentions,
                GROUP_CONCAT(DISTINCT s.source) AS sources
        FROM    signal_entities se
        JOIN    signals s ON s.signal_id = se.signal_id
        WHERE   se.label = 'PERSON'
          AND   s.source = 'pdf_infiltrator'
          AND   UPPER(se.text) NOT IN (
                    'MW','FRP','ALASKA','MYANMAR','ACTOR2',
                    'SOUTH AFRICA','SA','GAUTENG','LIMPOPO',
                    'MPUMALANGA','KWAZULU'
                )
        GROUP BY LOWER(se.text)
        HAVING  sig_count >= ?
        ORDER BY total_mentions DESC, sig_count DESC
    """, (min_signals,)).fetchall()

    print(f"[promote_staged_entities] Candidates from signal_entities: {len(rows)}")

    # ── Step 2: build existing actor name set for dedup ─────────────────────
    existing = {
        row["name"].lower().strip()
        for row in conn.execute("SELECT name FROM actors").fetchall()
    }
    print(f"[promote_staged_entities] Existing actors in DB: {len(existing)}")

    # ── Step 3: filter and prepare inserts ───────────────────────────────────
    to_insert: list[dict] = []
    rejected: list[tuple[str, str]] = []

    for row in rows:
        raw_text = row["text"] or ""
        cleaned  = _clean_text(raw_text)
        stripped = _strip_title(cleaned)
        # Prefer the stripped version if it looks better, but fall back
        candidate = stripped if stripped and " " in stripped else cleaned

        if not _is_valid_name(candidate):
            rejected.append((raw_text, "noise_filter"))
            continue

        if candidate.lower().strip() in existing:
            rejected.append((raw_text, "already_in_actors"))
            continue

        to_insert.append({
            "name":           candidate,
            "raw_text":       raw_text,
            "sig_count":      row["sig_count"],
            "total_mentions": row["total_mentions"],
            "sources":        row["sources"],
        })
        # Mark as existing to prevent duplicates within this batch
        existing.add(candidate.lower().strip())

    print(f"[promote_staged_entities] Accepted for promotion: {len(to_insert)}")
    print(f"[promote_staged_entities] Rejected: {len(rejected)}")

    if verbose:
        print("\n  Accepted:")
        for item in to_insert:
            print(f"    + {item['name']!r}  (sigs={item['sig_count']}, "
                  f"mentions={item['total_mentions']})")
        print("\n  Rejected:")
        for raw, reason in rejected[:20]:
            print(f"    x {raw!r}  ({reason})")
        if len(rejected) > 20:
            print(f"    … +{len(rejected)-20} more rejected")

    if dry_run:
        print(f"\n[DRY RUN] Would INSERT {len(to_insert)} actors")
        return {
            "status":    "dry_run",
            "accepted":  len(to_insert),
            "rejected":  len(rejected),
            "accepted_names": [i["name"] for i in to_insert],
        }

    # ── Step 4: INSERT into actors ────────────────────────────────────────────
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = 0
    skipped  = 0
    promoted_names: list[str] = []

    for item in to_insert:
        try:
            conn.execute("""
                INSERT INTO actors
                    (name, type, description, source_type, confidence_score, automated, created_at)
                VALUES (?, 'person', ?, 'promoted', ?, 1, ?)
            """, (
                item["name"],
                f"Promoted from signal_entities (pdf_infiltrator, "
                f"sigs={item['sig_count']}, mentions={item['total_mentions']})",
                min(0.5 + item["sig_count"] * 0.1, 0.95),   # confidence: 0.5 + 0.1/signal
                now,
            ))
            inserted += 1
            promoted_names.append(item["name"])
        except sqlite3.IntegrityError as e:
            skipped += 1
            if verbose:
                print(f"  SKIP {item['name']!r}: {e}")

    conn.commit()

    # ── Step 5: report ────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  PROMOTION COMPLETE")
    print(f"  Inserted:  {inserted}")
    print(f"  Skipped (integrity conflict): {skipped}")
    print(f"{'='*65}")
    print(f"\n  Promoted actors:")
    for name in promoted_names:
        print(f"    + {name}")

    # ── Step 6: log to pipeline_runs ──────────────────────────────────────────
    result = {
        "status":         "success",
        "inserted":       inserted,
        "skipped":        skipped,
        "rejected":       len(rejected),
        "promoted_names": promoted_names,
        "min_signals":    min_signals,
        "generated_at":   now,
    }
    try:
        conn.execute("""
            INSERT INTO pipeline_runs
                (component, status, records_in, records_out, duration_s, detail_json)
            VALUES ('promote_staged_entities', 'success', ?, ?, 0, ?)
        """, (len(rows), inserted, json.dumps(result)))
        conn.commit()
    except Exception as e:
        print(f"  Warning: pipeline_runs log failed: {e}")

    conn.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Actor Promotion Gate — promote signal_entities PERSON "
                    "entries into the actors table (P3.2-06)"
    )
    parser.add_argument("--db",          type=str, default=None)
    parser.add_argument(
        "--min-signals", type=int, default=1,
        help="Minimum distinct signals a PERSON entity must appear in (default: 1)"
    )
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--verbose",  action="store_true")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    result  = run(db_path, min_signals=args.min_signals,
                  dry_run=args.dry_run, verbose=args.verbose)
    print(json.dumps(result, indent=2))
    sys.exit(0)

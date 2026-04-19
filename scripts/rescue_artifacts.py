#!/usr/bin/env python
"""
scripts/rescue_artifacts.py — Landfill Rescue for Stuck Artifacts
==================================================================

Targets the P3-09 critical debt: ~548k artifacts stuck in processing_status
= 'no_intel' that were never re-processed after the entity resolver and NER
pipeline were improved.

Strategy
────────
1. Query artifacts WHERE processing_status = 'no_intel' AND raw_text_cache
   IS NOT NULL AND raw_text_cache != ''.
2. Score each artifact's source domain against the Admiralty tier list.
3. Promote A/B-tier artifacts to processing_status = 'A1-PENDING' — this
   flag is recognised by artifact_processor.py as "force re-process from
   raw_text_cache using the current NER pipeline".
4. Leave C/D/E/F-tier artifacts as 'no_intel' (or optionally promote with
   a lower-priority flag).
5. Report counts and log to pipeline_runs.

Processing statuses used
────────────────────────
  'no_intel'    — current state: zero intelligence extracted, skipped
  'A1-PENDING'  — rescue flag: re-process with current entity resolver
  'pending'     — original unprocessed state

The artifact_processor checks for 'A1-PENDING' rows and re-runs NER on
raw_text_cache instead of re-downloading the source document. This means
the rescue is purely CPU-bound, no network required.

Usage
─────
  python scripts/rescue_artifacts.py                    # promote A+B tier
  python scripts/rescue_artifacts.py --tier ABC         # include C tier
  python scripts/rescue_artifacts.py --dry-run          # count only
  python scripts/rescue_artifacts.py --batch-size 1000  # custom batch
  python scripts/rescue_artifacts.py --db /path/to/db

Safety
──────
  - Idempotent: re-running on already-promoted rows is a no-op (WHERE clause
    filters on 'no_intel' only).
  - Zero-downtime: UPDATE in batches of --batch-size with COMMIT per batch
    to avoid holding a long write lock.
  - Does NOT delete or overwrite raw_text_cache.
  - Does NOT touch artifacts without raw_text_cache (those need re-download).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Import Admiralty tier sets — must be importable from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from forage.utils.admiralty import A_TIER_DOMAINS, B_TIER_DOMAINS, grade_source
    _ADMIRALTY_AVAILABLE = True
except ImportError:
    _ADMIRALTY_AVAILABLE = False
    A_TIER_DOMAINS = frozenset()
    B_TIER_DOMAINS = frozenset()


# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_BATCH_SIZE = 2000

# processing_status value that signals artifact_processor to re-process
RESCUE_STATUS = "A1-PENDING"


# ── DB helpers ────────────────────────────────────────────────────────────────

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


# ── Domain extraction from metadata_json ─────────────────────────────────────

def _extract_domain(artifact: sqlite3.Row) -> str:
    """
    Resolve the Admiralty lookup key for an artifact.

    Resolution order
    ────────────────
    1. source='government'       → 'government'  (A-tier pdf_portal court orders)
    2. source='unverified'
         source_type='seed'      → 'npa'          (NPA sitemap bulk PDFs — all
                                                    titles confirm npa.gov.za origin)
         source_type='pdf_portal'→ 'government'   (ad-hoc portal drops)
         anything else           → ''             (unknown — skip for high tiers)
    3. All other source values   → source as-is  (matches admiralty short-names)
    """
    source      = (artifact["source"]      or "").strip().lower()
    source_type = (artifact["source_type"] or "").strip().lower()

    if source == "government":
        return "government"

    if source == "unverified":
        if source_type == "seed":
            # Seed data originates from the NPA sitemap crawler — every sampled
            # title confirms npa.gov.za provenance.
            return "npa"
        if source_type == "pdf_portal":
            return "government"
        # live/unknown unverified — return empty to skip for Tier A
        return ""

    return source


# ── Tier classification ───────────────────────────────────────────────────────

def _is_rescue_candidate(domain: str, tiers: str) -> bool:
    """
    Return True if the domain qualifies for rescue based on the tier filter.

    tiers: string of letters e.g. "AB" → A and B tier only
                                   "ABC" → A, B, and C tier
    """
    if not _ADMIRALTY_AVAILABLE:
        # Fallback: promote all artifacts with raw_text_cache
        return True

    grade = grade_source(domain)
    return grade in tiers.upper()


# ── Main rescue loop ──────────────────────────────────────────────────────────

def run(
    db_path: Path,
    tiers: str = "AB",
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    start = time.monotonic()

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _connect(db_path)

    # ── Pre-flight counts ─────────────────────────────────────────────────────
    total_no_intel = conn.execute(
        "SELECT COUNT(*) FROM artifacts WHERE processing_status = 'no_intel'"
    ).fetchone()[0]

    has_cache = conn.execute(
        "SELECT COUNT(*) FROM artifacts "
        "WHERE processing_status = 'no_intel' "
        "  AND raw_text_cache IS NOT NULL "
        "  AND raw_text_cache != ''"
    ).fetchone()[0]

    print(f"[rescue_artifacts] Database: {db_path}")
    print(f"[rescue_artifacts] Tiers to promote: {tiers.upper()}")
    print(f"[rescue_artifacts] Dry-run: {dry_run}")
    print(f"\nPre-flight:")
    print(f"  Total 'no_intel' artifacts:              {total_no_intel:>10,}")
    print(f"  Of which have raw_text_cache populated:  {has_cache:>10,}")
    print(f"  No raw_text_cache (cannot rescue):       {total_no_intel - has_cache:>10,}")
    print()

    if dry_run:
        # Count how many would be promoted without actually doing it
        promoted_estimate = 0
        offset = 0
        while True:
            rows = conn.execute("""
                SELECT artifact_id, source, source_type
                FROM   artifacts
                WHERE  processing_status = 'no_intel'
                  AND  raw_text_cache IS NOT NULL
                  AND  raw_text_cache != ''
                LIMIT  ? OFFSET ?
            """, (batch_size, offset)).fetchall()
            if not rows:
                break
            for row in rows:
                domain = _extract_domain(row)
                if _is_rescue_candidate(domain, tiers):
                    promoted_estimate += 1
            offset += batch_size
            if len(rows) < batch_size:
                break

        duration = round(time.monotonic() - start, 2)
        result = {
            "status":            "dry_run",
            "total_no_intel":    total_no_intel,
            "has_cache":         has_cache,
            "would_promote":     promoted_estimate,
            "tiers":             tiers.upper(),
            "duration_s":        duration,
        }
        print(f"[DRY RUN] Would promote {promoted_estimate:,} artifacts to '{RESCUE_STATUS}'")
        return result

    # ── Live promotion loop ───────────────────────────────────────────────────
    promoted   = 0
    skipped    = 0
    no_domain  = 0
    offset     = 0

    while True:
        rows = conn.execute("""
            SELECT artifact_id, source, source_type
            FROM   artifacts
            WHERE  processing_status = 'no_intel'
              AND  raw_text_cache IS NOT NULL
              AND  raw_text_cache != ''
            LIMIT  ? OFFSET ?
        """, (batch_size, offset)).fetchall()

        if not rows:
            break

        batch_ids: list[int] = []
        for row in rows:
            domain = _extract_domain(row)
            if not domain:
                no_domain += 1
                # Promote domainless artifacts too if tiers includes F
                if "F" in tiers.upper():
                    batch_ids.append(row["artifact_id"])
                else:
                    skipped += 1
                continue
            if _is_rescue_candidate(domain, tiers):
                batch_ids.append(row["artifact_id"])
            else:
                skipped += 1

        if batch_ids:
            ph = ",".join("?" * len(batch_ids))
            conn.execute(
                f"UPDATE artifacts SET processing_status = ? "
                f"WHERE artifact_id IN ({ph})",
                (RESCUE_STATUS, *batch_ids),
            )
            conn.commit()
            promoted += len(batch_ids)

        offset += batch_size
        if verbose or (offset % 10000 == 0):
            print(f"  Progress: {offset:,} scanned, {promoted:,} promoted, "
                  f"{skipped:,} skipped…")

        if len(rows) < batch_size:
            break

    # ── Final counts ──────────────────────────────────────────────────────────
    final_pending = conn.execute(
        f"SELECT COUNT(*) FROM artifacts WHERE processing_status = '{RESCUE_STATUS}'"
    ).fetchone()[0]

    duration = round(time.monotonic() - start, 2)

    print(f"\n[rescue_artifacts] Complete in {duration}s")
    print(f"  Promoted to '{RESCUE_STATUS}':  {promoted:,}")
    print(f"  Skipped (tier too low):          {skipped:,}")
    print(f"  No domain (left as no_intel):    {no_domain:,}")
    print(f"  Total '{RESCUE_STATUS}' in DB:   {final_pending:,}")

    result = {
        "status":          "success",
        "total_no_intel":  total_no_intel,
        "has_cache":       has_cache,
        "promoted":        promoted,
        "skipped":         skipped,
        "no_domain":       no_domain,
        "final_pending":   final_pending,
        "tiers":           tiers.upper(),
        "duration_s":      duration,
    }

    # Log to pipeline_runs
    try:
        conn.execute("""
            INSERT INTO pipeline_runs
                (component, status, records_in, records_out, duration_s, detail_json)
            VALUES ('rescue_artifacts', 'success', ?, ?, ?, ?)
        """, (has_cache, promoted, duration, json.dumps(result)))
        conn.commit()
    except Exception as e:
        print(f"  Warning: pipeline_runs log failed: {e}")

    conn.close()
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Landfill Rescue — promote no_intel artifacts to A1-PENDING"
    )
    parser.add_argument("--db",         type=str, default=None)
    parser.add_argument(
        "--tier", "--tiers",
        dest="tiers", type=str, default="AB",
        help="Admiralty source tiers to promote (default: AB). "
             "Use 'ABC' to include Fairly reliable sources, "
             "'ABCDEF' to promote all artifacts with raw_text_cache."
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Rows per UPDATE batch (default: {DEFAULT_BATCH_SIZE})"
    )
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--verbose",  action="store_true")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    result  = run(
        db_path,
        tiers=args.tiers,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0)

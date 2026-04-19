"""
scripts/artifact_triage.py — CT-1 artifact backlog triage.

Classifies 548K+ stuck 'pending' news stubs so the triple extractor
pipeline stops blocking on zero-content records.

Three-tier logic
────────────────
  Tier 0  < 50 chars text          → no_intel  (3,525 stubs)
  Tier 1  50–199 chars, no anchor  → no_intel  (544,323 stubs)
  Tier 2  200+ chars               → regex scan → done | no_intel (786)

"Anchored" = pinned to a case OR linked to a signal. Anchored records
are always skipped regardless of tier.

Usage
─────
  python scripts/artifact_triage.py            # dry-run (preview only)
  python scripts/artifact_triage.py --commit   # apply changes
  python scripts/artifact_triage.py --tier 2   # run only tier 2
"""

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"

# ── SA entity patterns for Tier 2 content scoring ────────────────────────────
# Matches things worth keeping: named orgs, rand amounts, case numbers,
# court names, government departments, legislation references.
_ENTITY_PATTERNS = re.compile(
    r"""
      R\s?\d[\d\s,.]*(?:million|billion|bn|m\b)   # rand amounts
    | \bCase\s+No\.?\s*\d+                         # case numbers
    | \b(?:NPA|SIU|Hawks|SAPS|AGSA|NT|SARB|SARS|CIPC|FSCA)\b  # SA agencies
    | \b(?:High\s+Court|Supreme\s+Court|Magistrate|Tribunal)\b  # courts
    | \b(?:Section|Act|Regulation)\s+\d+            # legal refs
    | \b(?:procurement|tender|corruption|bribery|fraud|money\s+laundering|state\s+capture)\b  # crime
    | \b(?:Minister|Premier|Director[-\s]General|Commissioner)\b  # titles
    | \b(?:Pty|Ltd|Holdings|Group|Inc)\b            # company suffixes
    | [A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\s+(?:said|told|confirmed|denied|arrested|charged)  # named source quotes
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _clean_text(raw: str) -> str:
    """Strip HTML entities and whitespace."""
    text = re.sub(r"&nbsp;|&amp;|&lt;|&gt;|&quot;", " ", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tier2_score(raw_text: str) -> bool:
    """Return True if text contains at least one SA entity worth keeping."""
    text = _clean_text(raw_text)
    return bool(_ENTITY_PATTERNS.search(text))


def run_triage(conn: sqlite3.Connection, commit: bool, tier_filter: int | None):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── Identify anchored artifact_ids (case-pinned or signal-linked) ─────────
    pinned = {r[0] for r in cur.execute(
        "SELECT DISTINCT artifact_id FROM case_artifacts"
    ).fetchall()}
    signal_linked = {r[0] for r in cur.execute(
        "SELECT DISTINCT source_artifact_id FROM signals "
        "WHERE source_artifact_id IS NOT NULL"
    ).fetchall()}
    anchored = pinned | signal_linked

    print(f"Anchored (protected): {len(anchored):,}  "
          f"({len(pinned)} case-pinned, {len(signal_linked)} signal-linked)")
    print()

    # ── Fetch all pending stubs in one pass ───────────────────────────────────
    print("Loading pending artifacts…")
    t0 = time.time()
    rows = cur.execute("""
        SELECT artifact_id, raw_text_cache
        FROM   artifacts
        WHERE  processing_status = 'pending'
    """).fetchall()
    print(f"Loaded {len(rows):,} pending artifacts in {time.time()-t0:.1f}s")
    print()

    # ── Classify ──────────────────────────────────────────────────────────────
    tier0_no_intel  = []   # < 50 chars
    tier1_no_intel  = []   # 50–199 chars, not anchored
    tier2_done      = []   # 200+ chars, entity match
    tier2_no_intel  = []   # 200+ chars, no entity match
    skipped         = []   # anchored regardless of length

    for r in rows:
        aid  = r["artifact_id"]
        text = r["raw_text_cache"] or ""
        tlen = len(text)

        if aid in anchored:
            skipped.append(aid)
            continue

        if tlen < 50:
            tier0_no_intel.append(aid)
        elif tlen < 200:
            tier1_no_intel.append(aid)
        else:
            if _tier2_score(text):
                tier2_done.append(aid)
            else:
                tier2_no_intel.append(aid)

    # ── Report ────────────────────────────────────────────────────────────────
    total_update = (len(tier0_no_intel) + len(tier1_no_intel)
                    + len(tier2_done) + len(tier2_no_intel))
    print("=" * 60)
    print(f"{'TIER':10} {'ACTION':15} {'COUNT':>10}")
    print("-" * 60)
    print(f"{'PROTECTED':10} {'skip':15} {len(skipped):>10,}")
    print(f"{'Tier 0':10} {'-> no_intel':15} {len(tier0_no_intel):>10,}")
    print(f"{'Tier 1':10} {'-> no_intel':15} {len(tier1_no_intel):>10,}")
    print(f"{'Tier 2':10} {'-> done':15} {len(tier2_done):>10,}")
    print(f"{'Tier 2':10} {'-> no_intel':15} {len(tier2_no_intel):>10,}")
    print("-" * 60)
    print(f"{'TOTAL':10} {'will update':15} {total_update:>10,}")
    print("=" * 60)
    print()

    if not commit:
        print("DRY RUN — no changes written. Re-run with --commit to apply.")
        return

    # ── Apply ─────────────────────────────────────────────────────────────────
    print("Applying… (this may take a few seconds)")
    t1 = time.time()

    def _bulk_update(ids, status, tier_name):
        if tier_filter is not None and tier_filter != int(tier_name[-1]):
            return
        if not ids:
            return
        CHUNK = 10_000
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i+CHUNK]
            ph = ",".join("?" * len(chunk))
            cur.execute(
                f"UPDATE artifacts SET processing_status=? WHERE artifact_id IN ({ph})",
                [status] + chunk,
            )
        print(f"  {tier_name}: {len(ids):,} -> {status}")

    conn.execute("BEGIN")
    try:
        _bulk_update(tier0_no_intel, "no_intel", "Tier0")
        _bulk_update(tier1_no_intel, "no_intel", "Tier1")
        _bulk_update(tier2_no_intel, "no_intel", "Tier2")
        _bulk_update(tier2_done,     "done",     "Tier2")
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        print(f"ERROR — rolled back: {exc}", file=sys.stderr)
        sys.exit(1)

    elapsed = time.time() - t1
    print(f"\nDone in {elapsed:.1f}s")

    # ── Post-commit verification ──────────────────────────────────────────────
    print()
    print("=== Post-triage status counts ===")
    for row in conn.execute(
        "SELECT processing_status, COUNT(*) as n FROM artifacts "
        "GROUP BY processing_status ORDER BY n DESC"
    ).fetchall():
        print(f"  {row[0]}: {row[1]:,}")


def main():
    parser = argparse.ArgumentParser(description="FORGE artifact backlog triage")
    parser.add_argument("--commit", action="store_true",
                        help="Write changes (default is dry-run)")
    parser.add_argument("--tier", type=int, choices=[0, 1, 2],
                        help="Run only this tier (default: all)")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        run_triage(conn, commit=args.commit, tier_filter=args.tier)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — Corpus Attribution Bridge  (flux/processors/corpus_builder.py)
════════════════════════════════════════════════════════════════════════════
Bridges x_pulse collection → actor socint_profile corpus.

x_pulse writes tweets to socint_signals with actor_id = NULL.
This processor:
  1. Groups collected signals by X handle (via metadata_json)
  2. Finds the matching actor record — first by existing socint_profile
     handle associations, then by actor name substring match
  3. Optionally creates new actor records (--create-actors) for handles
     with no match
  4. Builds / merges the rolling 100-sample corpus into actors.socint_profile
  5. Back-fills socint_signals.actor_id so future joins work correctly

Once at least 2 actors have corpus_ready=True (≥7 samples, ≥2000 chars),
flux/processors/resonance.py can run and the SOCINT dossier panels on actor
detail pages will display real fingerprint data.

Usage
─────
  python flux/processors/corpus_builder.py
  python flux/processors/corpus_builder.py --create-actors
  python flux/processors/corpus_builder.py --dry-run
  python flux/processors/corpus_builder.py --dry-run --create-actors
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Bootstrap project root ────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("FORGE_DB", str(BASE_DIR / "database.db")))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [corpus_builder] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
log = logging.getLogger("forge.flux.corpus_builder")

# ── Constants (must match flux/processors/stylometric.py) ─────────────────────

CORPUS_MAX_ITEMS = 100   # rolling window cap — oldest samples evicted first
CORPUS_MIN_ITEMS = 7     # corpus gate: minimum samples for resonance
CORPUS_MIN_CHARS = 2000  # corpus gate: minimum total characters for resonance

# ── Actor type inference from handle keywords ─────────────────────────────────
# Lower-precedence → higher-precedence (later entries win on substring match)

_TYPE_HINTS: list[tuple[str, str]] = [
    # media
    ("news",        "media"),
    ("media",       "media"),
    ("radio",       "media"),
    ("sabc",        "media"),
    ("ewn",         "media"),
    ("enca",        "media"),
    ("timeslive",   "media"),
    ("dailymaverick","media"),
    ("sowetan",     "media"),
    ("heraldlive",  "media"),
    ("dispatch",    "media"),
    # government
    ("sars",        "government"),
    ("treasury",    "government"),
    ("presidency",  "government"),
    ("government",  "government"),
    ("parliament",  "government"),
    ("minister",    "government"),
    ("dept",        "government"),
    ("dti",         "government"),
    ("dirco",       "government"),
    ("police",      "government"),
    ("saps",        "government"),
    ("npa",         "government"),
    ("hawks",       "government"),
    ("siu",         "government"),
]


def _infer_actor_type(handle_norm: str) -> str:
    """Return the best-guess actor type for an X handle. Defaults to 'organization'."""
    h = handle_norm.lower().replace("_", "").replace(".", "")
    result = "organization"
    for keyword, atype in _TYPE_HINTS:
        if keyword in h:
            result = atype
    return result


# ── Phase 1: Load socint_signals grouped by handle ───────────────────────────

def _load_handle_signals(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """
    Return all x_pulse socint_signals grouped by normalised handle.
    { "sars_za": [{ss_id, signal_id, content, timestamp, display_name}, ...] }
    Signals are ordered newest-first within each group.
    """
    rows = conn.execute("""
        SELECT
            id            AS ss_id,
            signal_id,
            content,
            metadata_json,
            timestamp
        FROM  socint_signals
        WHERE source = 'x_pulse'
          AND content IS NOT NULL
          AND content != ''
        ORDER BY timestamp DESC
    """).fetchall()

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except Exception:
            meta = {}

        handle_raw = meta.get("x_handle", "").strip()
        if not handle_raw:
            continue

        handle_norm = handle_raw.lower().lstrip("@").strip()
        if not handle_norm:
            continue

        grouped.setdefault(handle_norm, []).append({
            "ss_id":        r["ss_id"],
            "signal_id":    r["signal_id"],
            "content":      r["content"],
            "timestamp":    r["timestamp"],
            "display_name": meta.get("x_display_name", handle_norm),
        })

    return grouped


# ── Phase 2: Actor resolution ─────────────────────────────────────────────────

def _find_actor_by_handle(conn: sqlite3.Connection, handle_norm: str) -> int | None:
    """
    Search actors whose socint_profile already lists this handle.
    Returns actor_id on match, None otherwise.
    """
    rows = conn.execute(
        "SELECT actor_id, socint_profile FROM actors WHERE socint_profile IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            profile = json.loads(row["socint_profile"])
        except Exception:
            continue
        existing = [h.lower().lstrip("@") for h in profile.get("x_handles", [])]
        if handle_norm in existing:
            return row["actor_id"]
    return None


def _find_actor_by_name(conn: sqlite3.Connection, handle_norm: str) -> int | None:
    """
    Fuzzy fallback: substring match on actors.name using handle components.
    e.g. 'sapoliceservice' → checks if any actor name contains 'sapolice' or 'police'
    Only fires when handle is ≥ 4 chars and the match is unambiguous (exactly 1 hit).
    Returns actor_id on unique match, None otherwise.
    """
    if len(handle_norm) < 4:
        return None

    # Try with the full handle and with common SA suffix strips (_za, za, _rsa, rsa)
    candidates = [handle_norm]
    for suffix in ("_za", "za", "_rsa", "rsa", "_sa", "sa"):
        if handle_norm.endswith(suffix) and len(handle_norm) > len(suffix) + 3:
            candidates.append(handle_norm[: -len(suffix)])

    for candidate in candidates:
        # Replace underscores for readability
        needle = candidate.replace("_", " ")
        rows = conn.execute(
            "SELECT actor_id FROM actors WHERE lower(name) LIKE ?",
            (f"%{needle}%",),
        ).fetchall()
        if len(rows) == 1:
            return rows[0]["actor_id"]

    return None


def _create_actor(
    conn: sqlite3.Connection,
    handle_norm: str,
    display_name: str,
) -> int:
    """
    Insert a minimal actor record for an unmatched X handle.
    Returns the new actor_id.
    """
    actor_type = _infer_actor_type(handle_norm)
    now = datetime.now(timezone.utc).isoformat() + "Z"

    # Prefer the display name when it's different from the handle;
    # otherwise title-case the handle
    if display_name and display_name.lower() != handle_norm:
        name = display_name
    else:
        name = handle_norm.replace("_", " ").title()

    cur = conn.execute(
        """
        INSERT INTO actors (name, type, description, source_type, created_at)
        VALUES (?, ?, ?, 'live', ?)
        """,
        (
            f"@{handle_norm}",
            actor_type,
            f"Auto-created by FLUX corpus_builder from X handle @{handle_norm}. "
            f"Display name: {name}",
            now,
        ),
    )
    return cur.lastrowid


# ── Phase 3: Build corpus ─────────────────────────────────────────────────────

def _build_profile(
    signals:          list[dict],
    existing_profile: dict,
    handle_norm:      str,
) -> dict:
    """
    Merge new signal content into an existing socint_profile dict.

    Strategy:
    - New samples prepended (signals already sorted newest-first)
    - Duplicate content de-duplicated (exact match)
    - Rolling window capped at CORPUS_MAX_ITEMS (oldest drop off the end)
    - x_handles / x_display_names accumulated across runs
    """
    existing_corpus: list[str] = existing_profile.get("corpus", [])
    existing_set:    set[str]  = set(existing_corpus)

    # Collect new-to-us content (preserve insertion order, deduplicate)
    new_items:     list[str] = []
    display_names: set[str]  = set(existing_profile.get("x_display_names", []))

    for sig in signals:
        content = (sig.get("content") or "").strip()
        if content and content not in existing_set:
            new_items.append(content)
            existing_set.add(content)

        dn = (sig.get("display_name") or "").strip()
        if dn and dn.lower() != handle_norm:
            display_names.add(dn)

    # Merge: newest first; stable dedup preserves order
    merged = new_items + existing_corpus
    seen:   set[str]  = set()
    deduped: list[str] = []
    for item in merged:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    corpus = deduped[:CORPUS_MAX_ITEMS]

    # Handles list — canonical @handle first
    handle_with_at = f"@{handle_norm}"
    existing_handles = existing_profile.get("x_handles", [])
    handles_merged = [handle_with_at] + [
        h for h in existing_handles if h != handle_with_at
    ]

    return {
        "corpus":          corpus,
        "x_handles":       handles_merged,
        "x_display_names": sorted(display_names),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def run(dry_run: bool = False, create_actors: bool = False) -> dict:
    """
    Main attribution pass.

    dry_run=True  — compute and log everything; write nothing to DB.
    create_actors — insert new actor records for handles with no existing match.

    Returns a summary dict.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        log.info("=== FLUX Corpus Attribution Bridge ===")
        log.info("DB            : %s", DB_PATH)
        log.info("Dry run       : %s", dry_run)
        log.info("Create actors : %s", create_actors)

        # ── Phase 1 ───────────────────────────────────────────────────────────
        log.info("=== Phase 1: Grouping signals by handle ===")
        handle_map = _load_handle_signals(conn)
        log.info("Unique handles found : %d", len(handle_map))

        matched    = 0
        created    = 0
        skipped    = 0
        updated    = 0
        backfilled = 0
        corpus_ready_count = 0

        # ── Phase 2 + 3 ───────────────────────────────────────────────────────
        log.info("=== Phase 2: Actor resolution + corpus build ===")

        for handle_norm, signals in sorted(handle_map.items()):
            # --- Try to resolve actor ------------------------------------------
            actor_id: int | None = _find_actor_by_handle(conn, handle_norm)

            if actor_id is None:
                actor_id = _find_actor_by_name(conn, handle_norm)
                if actor_id is not None:
                    log.info(
                        "Name-matched  @%-25s → actor_id=%-5d  signals=%d",
                        handle_norm, actor_id, len(signals),
                    )
                    matched += 1

            else:
                log.info(
                    "Handle-matched @%-24s → actor_id=%-5d  signals=%d",
                    handle_norm, actor_id, len(signals),
                )
                matched += 1

            if actor_id is None:
                if create_actors:
                    display_name = signals[0]["display_name"] if signals else handle_norm
                    if not dry_run:
                        actor_id = _create_actor(conn, handle_norm, display_name)
                        log.info(
                            "Created actor  @%-24s  id=%-5d  type=%s  signals=%d",
                            handle_norm, actor_id,
                            _infer_actor_type(handle_norm), len(signals),
                        )
                    else:
                        log.info(
                            "[DRY-RUN] Would create actor @%s  type=%s  signals=%d",
                            handle_norm, _infer_actor_type(handle_norm), len(signals),
                        )
                        actor_id = -1   # sentinel: skip DB writes but continue logging
                    created += 1
                else:
                    log.warning(
                        "No actor for   @%-24s  (%d signals) "
                        "— pass --create-actors to auto-create",
                        handle_norm, len(signals),
                    )
                    skipped += 1
                    continue

            # --- Load existing profile ----------------------------------------
            existing_raw: str | None = None
            if actor_id and actor_id > 0:
                row = conn.execute(
                    "SELECT socint_profile FROM actors WHERE actor_id = ?",
                    (actor_id,),
                ).fetchone()
                existing_raw = row["socint_profile"] if row else None

            try:
                existing_profile = json.loads(existing_raw or "{}")
            except Exception:
                existing_profile = {}

            # --- Build / merge corpus -----------------------------------------
            profile = _build_profile(signals, existing_profile, handle_norm)

            total_chars  = sum(len(s) for s in profile["corpus"])
            is_ready     = (
                len(profile["corpus"]) >= CORPUS_MIN_ITEMS
                and total_chars >= CORPUS_MIN_CHARS
            )
            if is_ready:
                corpus_ready_count += 1

            log.info(
                "  @%-24s  corpus=%3d  chars=%5d  ready=%s",
                handle_norm,
                len(profile["corpus"]),
                total_chars,
                "YES" if is_ready else "no",
            )

            # --- Write --------------------------------------------------------
            if not dry_run and actor_id and actor_id > 0:
                conn.execute(
                    "UPDATE actors SET socint_profile = ? WHERE actor_id = ?",
                    (json.dumps(profile), actor_id),
                )
                updated += 1

                # Back-fill actor_id on all matching socint_signals rows
                ss_ids = [s["ss_id"] for s in signals]
                if ss_ids:
                    placeholders = ",".join("?" * len(ss_ids))
                    conn.execute(
                        f"UPDATE socint_signals SET actor_id = ? "
                        f"WHERE id IN ({placeholders})",
                        [actor_id] + ss_ids,
                    )
                    backfilled += len(ss_ids)

        if not dry_run:
            conn.commit()

        summary = {
            "status":             "done",
            "handles_found":      len(handle_map),
            "matched":            matched,
            "created":            created,
            "skipped":            skipped,
            "profiles_updated":   updated,
            "signals_backfilled": backfilled,
            "corpus_ready":       corpus_ready_count,
            "dry_run":            dry_run,
        }
        log.info("=== Complete: %s ===", summary)
        return summary

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE FLUX — Corpus Attribution Bridge"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database",
    )
    parser.add_argument(
        "--create-actors",
        action="store_true",
        help="Create new actor records for X handles with no existing match",
    )
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, create_actors=args.create_actors)
    sys.exit(0 if result["status"] == "done" else 1)

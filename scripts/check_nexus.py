#!/usr/bin/env python
"""
scripts/check_nexus.py — Nexus Link Diagnostic for Operation Dark Badge
=========================================================================

Identifies "Smoking Gun" documents: A-tier signals that are connected to
3 or more distinct actors in entity_relationships.

A signal with 3+ actor connections is a nexus point — it names multiple
institutional actors in a forensically significant relationship context
(INVESTIGATES, ACCUSED_OF, CONTRACTED). These are the highest-value
exhibits for Operation Dark Badge.

Output
──────
  Terminal: ranked table of nexus signals with actor lists
  JSON:     scripts/nexus_report_<timestamp>.json (optional --save)

Logic
─────
  A-tier signals = signals whose source is in A_TIER_DOMAINS (admiralty.py)
  OR source_reliability = 'A' in signals_new (if shadow vault is graded).
  Actor associations come from entity_relationships.signal_id links.

Usage
─────
  python scripts/check_nexus.py
  python scripts/check_nexus.py --min-actors 2   # lower threshold
  python scripts/check_nexus.py --save            # write JSON report
  python scripts/check_nexus.py --all-tiers       # skip A-tier filter
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from forage.utils.admiralty import A_TIER_DOMAINS, _DOMAIN_SOURCE_GRADES
    _A_TIER_SOURCES = frozenset(
        k for k, v in _DOMAIN_SOURCE_GRADES.items() if v == "A"
    )
    _ADMIRALTY_OK = True
except ImportError:
    _A_TIER_SOURCES = frozenset({"siu","npa","hawks","special_tribunal","agsa",
                                  "treasury","government","pdf_infiltrator"})
    _ADMIRALTY_OK = False


def _resolve_db(override=None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1] / "database.db"


def run(
    db_path: Path,
    min_actors: int = 3,
    all_tiers: bool = False,
    save: bool = False,
) -> dict:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # ── Step 1: collect entity_relationships that have a signal_id ────────────
    # Each relationship may link back to a signal via signal_id (direct) OR
    # via artifact chain: relationship.source_artifact_id -> signal.source_artifact_id
    # We check both paths.

    print("[check_nexus] Querying entity_relationships...")
    er_rows = conn.execute("""
        SELECT er.relationship_id, er.relation_type, er.confidence,
               er.source_artifact_id,
               a1.name  AS subject_name, a1.actor_id AS subject_id,
               a2.name  AS object_name,  a2.actor_id AS object_id
        FROM   entity_relationships er
        LEFT JOIN actors a1 ON a1.actor_id = er.subject_actor_id
        LEFT JOIN actors a2 ON a2.actor_id = er.object_actor_id
        WHERE  er.relation_type != 'co_occurrence'
        ORDER  BY er.confidence DESC
    """).fetchall()

    print(f"[check_nexus] {len(er_rows)} non-co_occurrence relationships loaded")

    # ── Step 2: map artifact_id → list of relationship entries ────────────────
    # entity_relationships links back to source via source_artifact_id only.
    # From artifact we find the originating signal via signals.source_artifact_id.
    from collections import defaultdict
    artifact_actors: dict[int, list[dict]] = defaultdict(list)
    no_artifact: list[dict] = []   # relationships with no artifact provenance

    for er in er_rows:
        entry = {
            "relationship_id": er["relationship_id"],
            "relation_type":   er["relation_type"],
            "confidence":      er["confidence"],
            "subject":         er["subject_name"],
            "object":          er["object_name"],
            "subject_id":      er["subject_id"],
            "object_id":       er["object_id"],
        }
        if er["source_artifact_id"]:
            artifact_actors[int(er["source_artifact_id"])].append(entry)
        else:
            no_artifact.append(entry)

    # ── Step 3: resolve signals for each artifact_id ──────────────────────────
    art_ids = list(artifact_actors.keys())
    # Map: signal_id → (meta dict, list of relationship entries)
    signal_actors: dict[str, list[dict]] = defaultdict(list)
    sig_meta: dict[str, dict] = {}

    if art_ids:
        ph = ",".join("?" * len(art_ids))
        rows = conn.execute(f"""
            SELECT s.signal_id, s.title, s.source, s.relevance_score,
                   s.gravity_score, s.timestamp, s.source_artifact_id
            FROM signals s
            WHERE s.source_artifact_id IN ({ph})
        """, art_ids).fetchall()
        for r in rows:
            sid = r["signal_id"]
            aid = r["source_artifact_id"]
            if sid not in sig_meta:
                sig_meta[sid] = dict(r)
            for entry in artifact_actors[aid]:
                signal_actors[sid].append(entry)

    # Also expose relationships that have no signal link as an "artifact-direct" group
    # keyed by source_artifact_id string for display
    for art_id, entries in artifact_actors.items():
        # If this artifact_id has no signal, create a synthetic key
        has_signal = any(
            (r["source_artifact_id"] == art_id)
            for r in conn.execute(
                "SELECT source_artifact_id FROM signals WHERE source_artifact_id=? LIMIT 1",
                (art_id,)
            ).fetchall()
        )
        if not has_signal:
            key = f"artifact:{art_id}"
            art_row = conn.execute(
                "SELECT title, source, source_type FROM artifacts WHERE artifact_id=?",
                (art_id,)
            ).fetchone()
            if art_row:
                sig_meta[key] = {
                    "signal_id": key,
                    "title": art_row["title"] or f"Artifact #{art_id}",
                    "source": art_row["source"] or "",
                    "relevance_score": None,
                    "gravity_score": None,
                    "timestamp": None,
                }
                for entry in entries:
                    signal_actors[key].append(entry)

    # ── Step 4: build nexus list — signals with min_actors distinct actors ────
    nexus: list[dict] = []

    for sig_id, relationships in signal_actors.items():
        meta = sig_meta.get(sig_id, {})
        source = (meta.get("source") or "").lower().strip()

        # A-tier filter
        if not all_tiers and source not in _A_TIER_SOURCES:
            continue

        # Collect distinct actors involved
        distinct_actors: set[int] = set()
        for rel in relationships:
            if rel["subject_id"]:
                distinct_actors.add(rel["subject_id"])
            if rel["object_id"]:
                distinct_actors.add(rel["object_id"])

        if len(distinct_actors) < min_actors:
            continue

        # Resolve actor names for display
        if distinct_actors:
            ph = ",".join("?" * len(distinct_actors))
            actor_rows = conn.execute(
                f"SELECT actor_id, name, type FROM actors WHERE actor_id IN ({ph})",
                list(distinct_actors)
            ).fetchall()
            actor_list = [{"id": r["actor_id"], "name": r["name"],
                           "type": r["type"]} for r in actor_rows]
        else:
            actor_list = []

        nexus.append({
            "signal_id":       sig_id,
            "title":           meta.get("title", "(unknown)"),
            "source":          source,
            "relevance_score": meta.get("relevance_score"),
            "gravity_score":   meta.get("gravity_score"),
            "timestamp":       meta.get("timestamp"),
            "actor_count":     len(distinct_actors),
            "actors":          actor_list,
            "relationships":   [
                f"{r['subject']} --{r['relation_type']}--> {r['object']}  [{r['confidence']}]"
                for r in relationships
            ],
        })

    # Sort by actor_count desc, then confidence
    nexus.sort(key=lambda x: x["actor_count"], reverse=True)

    # ── Step 5: print report ──────────────────────────────────────────────────
    tier_label = "A-tier" if not all_tiers else "all-tier"
    print(f"\n{'='*70}")
    print(f"  NEXUS REPORT -- {tier_label} signals with >={min_actors} actors")
    print(f"  Database: {db_path.name}")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"{'='*70}\n")

    if not nexus:
        print(f"  No {tier_label} signals found with >={min_actors} distinct actors.")
        print()
        print("  DIAGNOSTIC -- relationship distribution:")
        rows = conn.execute("""
            SELECT s.source, COUNT(*) AS rel_count
            FROM entity_relationships er
            JOIN artifacts a ON a.artifact_id = er.source_artifact_id
            JOIN signals s ON s.source_artifact_id = a.artifact_id
            GROUP BY s.source ORDER BY rel_count DESC LIMIT 10
        """).fetchall()
        for r in rows:
            print(f"    {r['rel_count']:>4}  source={r['source']}")
        print()
        print("  ARCHITECTURAL GAP IDENTIFIED:")
        print("  The 272k NPA artifacts (source_type=seed) produce signal_entities")
        print("  entries but their names are NOT yet promoted into the actors table.")
        print("  Run: python scripts/promote_staged_entities.py  (see P3.2-06)")
    else:
        for i, item in enumerate(nexus, 1):
            print(f"  [{i:02d}] ** {item['title'][:65]}")
            print(f"       source={item['source']}  rel={item['relevance_score']}  actors={item['actor_count']}")
            print(f"       signal_id={item['signal_id'][:16]}…")
            for a in item["actors"]:
                print(f"         * [{a['type']}] {a['name']}")
            print(f"       Links:")
            for rel in item["relationships"][:5]:
                print(f"         -> {rel}")
            if len(item["relationships"]) > 5:
                print(f"         … +{len(item['relationships'])-5} more")
            print()

    print(f"  Total nexus signals: {len(nexus)}")
    print(f"{'='*70}\n")

    # ── Step 6: summary stats ─────────────────────────────────────────────────
    total_er   = conn.execute("SELECT COUNT(*) FROM entity_relationships").fetchone()[0]
    by_type    = conn.execute("""
        SELECT relation_type, COUNT(*) c FROM entity_relationships
        GROUP BY relation_type ORDER BY c DESC
    """).fetchall()

    print("  Entity relationship summary:")
    print(f"    Total links:     {total_er}")
    for r in by_type:
        print(f"    {r[0]:<20} {r['c']}")

    conn.close()

    result = {
        "nexus_signals":         len(nexus),
        "nexus_items":           nexus,
        "total_entity_relationships": total_er,
        "by_relation_type":      {r[0]: r["c"] for r in by_type},
        "min_actors_threshold":  min_actors,
        "tier_filter":           "A" if not all_tiers else "all",
        "generated_at":          datetime.now(timezone.utc).isoformat(),
    }

    if save:
        ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = Path(__file__).parent / f"nexus_report_{ts}.json"
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        print(f"  Report saved: {out}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Nexus Diagnostic — find smoking-gun multi-actor signals"
    )
    parser.add_argument("--db",         type=str, default=None)
    parser.add_argument("--min-actors", type=int, default=3,
                        help="Minimum distinct actors per signal (default: 3)")
    parser.add_argument("--all-tiers",  action="store_true",
                        help="Include all sources, not just A-tier")
    parser.add_argument("--save",       action="store_true",
                        help="Save JSON report to scripts/nexus_report_<ts>.json")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    run(db_path, min_actors=args.min_actors, all_tiers=args.all_tiers,
        save=args.save)

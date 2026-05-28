#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — Resonance Batch Engine  (flux/processors/resonance.py)
════════════════════════════════════════════════════════════════════
O(n²) pairwise stylometric comparison across all actors whose corpus
has passed the readiness gate. Designed to run as a scheduled batch
job — NEVER inline with signal ingestion.

Architecture
────────────
  run(db_path, dry_run, threshold, graph_threshold)
      Main entry point. Returns a summary dict.

  Phase 1 — Corpus load
      Fetch all actors with non-NULL socint_profile and extract a
      composite fingerprint per actor (merges all corpus samples into
      one representative vector).

  Phase 2 — Pairwise comparison  [O(n²)]
      For every pair (a, b) where actor_a < actor_b (enforces the
      CHECK constraint on socint_resonance):
        • compare_fingerprints(fp_a, fp_b) → score
        • score >= RESONANCE_THRESHOLD → UPSERT socint_resonance
        • score >= GRAPH_INJECT_THRESHOLD → UPSERT entity_relationships
          with relation_type='stylometric_match', confidence=score

  Phase 3 — C-SOCINT cluster pass
      Build a subgraph from socint_resonance rows above threshold.
      Run greedy_modularity_communities (NetworkX, same dep as graph_engine).
      Write community IDs to actor_network_metrics.community_id_socint.
      Falls back gracefully if NetworkX is absent.

Tech-Debt Audit (actor_a < actor_b)
─────────────────────────────────────
  The socint_resonance table has:
      CHECK(actor_a < actor_b)
      UNIQUE(actor_a, actor_b)
  This prevents mirrored duplicates. This engine enforces the constraint
  by always assigning min(a,b) → actor_a and max(a,b) → actor_b before
  any INSERT or SELECT. The CHECK constraint is the DB-level backstop;
  this engine is the application-level guarantee.

Usage
─────
  python flux/processors/resonance.py
  python flux/processors/resonance.py --dry-run
  python flux/processors/resonance.py --threshold 0.70
  GRAPH_INJECT_THRESHOLD (default 0.70) can be overridden via env:
      FLUX_GRAPH_THRESHOLD=0.75 python flux/processors/resonance.py
"""

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths & sys.path bootstrap ────────────────────────────────────────────────
# When run directly (python flux/processors/resonance.py), Python adds the
# script's directory to sys.path — not the project root. Insert the root so
# the `flux` and `forge_modules` packages are importable without installation.

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH  = Path(os.environ.get("FORGE_DB", str(BASE_DIR / "database.db")))

# ── Thresholds ────────────────────────────────────────────────────────────────

RESONANCE_THRESHOLD   = float(os.environ.get("FLUX_RESONANCE_THRESHOLD", "0.65"))
GRAPH_INJECT_THRESHOLD = float(os.environ.get("FLUX_GRAPH_THRESHOLD",    "0.70"))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [resonance] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
log = logging.getLogger("forge.flux.resonance")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


# ── Import stylometric engine ─────────────────────────────────────────────────

try:
    from flux.processors.stylometric import (
        compare_fingerprints,
        extract_fingerprint,
        corpus_from_profile,
        _corpus_ready,
        CORPUS_MIN_ITEMS,
        CORPUS_MIN_CHARS,
    )
except ImportError as exc:
    log.error("Cannot import stylometric engine: %s", exc)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Corpus fingerprint builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_corpus_fingerprint(corpus: list[str]) -> Optional[dict]:
    """
    Merge all text samples in a corpus into one composite fingerprint
    suitable for compare_fingerprints().

    Returns None when corpus does not pass the readiness gate.

    Strategy
    ────────
      text_norm    : concatenation of all normalised samples (for SequenceMatcher)
      cashtags     : union of all cashtag sets
      emoji_bigrams: sum of all emoji bigram Counters
      caps_ratio   : mean across all samples
      leet_density : mean across all samples

    This deliberately over-represents the actor's dominant stylistic
    features — if they always use $ZAR, the union cashtag set reflects it.
    """
    if not _corpus_ready(corpus):
        return None

    per_sample_fps = [extract_fingerprint(t) for t in corpus]

    merged_norm   = " ".join(fp["text_norm"] for fp in per_sample_fps)
    merged_cash   = sorted(set(t for fp in per_sample_fps for t in fp["cashtags"]))
    merged_bigrams: Counter = Counter()
    for fp in per_sample_fps:
        merged_bigrams.update(Counter(fp["emoji_bigrams"]))

    caps_vals  = [fp["caps_ratio"]   for fp in per_sample_fps]
    leet_vals  = [fp["leet_density"] for fp in per_sample_fps]
    mean_caps  = sum(caps_vals)  / len(caps_vals)  if caps_vals  else 0.0
    mean_leet  = sum(leet_vals)  / len(leet_vals)  if leet_vals  else 0.0

    return {
        "text_norm":     merged_norm,
        "cashtags":      merged_cash,
        "emoji_bigrams": dict(merged_bigrams),
        "caps_ratio":    round(mean_caps, 6),
        "leet_density":  round(mean_leet, 6),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Load actors with ready corpora
# ─────────────────────────────────────────────────────────────────────────────

def _load_actor_fingerprints(conn: sqlite3.Connection) -> list[dict]:
    """
    Fetch all actors with a non-NULL socint_profile and build a composite
    fingerprint for each one whose corpus passes the readiness gate.

    Returns list of:
        { actor_id, name, fp: dict }
    Only actors with a READY corpus are included.
    """
    rows = conn.execute(
        "SELECT actor_id, name, socint_profile "
        "FROM actors "
        "WHERE socint_profile IS NOT NULL"
    ).fetchall()

    ready: list[dict] = []
    skipped = 0
    for row in rows:
        corpus = corpus_from_profile(row["socint_profile"])
        fp     = _build_corpus_fingerprint(corpus)
        if fp is None:
            skipped += 1
            continue
        ready.append({
            "actor_id": row["actor_id"],
            "name":     row["name"],
            "fp":       fp,
            "corpus_size": len(corpus),
            "corpus_chars": sum(len(t) for t in corpus),
        })

    log.info(
        "Actors with socint_profile: %d  |  corpus-ready: %d  |  skipped (thin): %d",
        len(rows), len(ready), skipped,
    )
    return ready


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Pairwise comparison + persistence
# ─────────────────────────────────────────────────────────────────────────────

def _ordered_pair(a: int, b: int) -> tuple[int, int]:
    """
    Enforce actor_a < actor_b for all DB operations.
    This satisfies the CHECK constraint and the UNIQUE key on socint_resonance.
    """
    return (min(a, b), max(a, b))


def _upsert_resonance(
    conn: sqlite3.Connection,
    actor_a: int,
    actor_b: int,
    score: float,
    features: dict,
) -> None:
    """Upsert a pairwise score into socint_resonance. Enforces a < b."""
    a, b = _ordered_pair(actor_a, actor_b)
    conn.execute(
        """
        INSERT INTO socint_resonance (actor_a, actor_b, score, features_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(actor_a, actor_b) DO UPDATE SET
            score         = excluded.score,
            features_json = excluded.features_json,
            updated_at    = excluded.updated_at
        """,
        (a, b, round(score, 6), json.dumps(features), _ts()),
    )


def _upsert_graph_edge(
    conn: sqlite3.Connection,
    actor_a: int,
    actor_b: int,
    score: float,
) -> None:
    """
    Inject a stylometric edge into entity_relationships.
    The graph engine's _load_relationship_edges() picks this up automatically
    on the next graph_engine.run() call — no changes to graph_engine needed.
    """
    conn.execute(
        """
        INSERT INTO entity_relationships
            (subject_actor_id, object_actor_id, relation_type,
             description, confidence, extraction_method, created_at)
        VALUES (?, ?, 'stylometric_match',
                'FLUX: stylometric resonance score ' || ?,
                ?, 'manual', ?)
        ON CONFLICT DO NOTHING
        """,
        (actor_a, actor_b, round(score, 4), round(score, 6), _ts()),
    )


def _run_pairwise(
    actors:           list[dict],
    conn:             sqlite3.Connection,
    resonance_thresh: float,
    graph_thresh:     float,
    dry_run:          bool,
) -> dict:
    """
    O(n²) pairwise comparison across all actors with ready corpora.
    Returns counts dict.
    """
    n           = len(actors)
    total_pairs = n * (n - 1) // 2
    compared    = 0
    above_res   = 0
    above_graph = 0

    log.info(
        "Pairwise pass: %d actors → %d pairs  "
        "(resonance_thresh=%.2f  graph_thresh=%.2f)",
        n, total_pairs, resonance_thresh, graph_thresh,
    )

    for i in range(n):
        for j in range(i + 1, n):
            a  = actors[i]
            b  = actors[j]
            aid, bid = a["actor_id"], b["actor_id"]

            score = compare_fingerprints(a["fp"], b["fp"])
            compared += 1

            if score < resonance_thresh:
                continue
            above_res += 1

            features = {
                "actor_a_name":   a["name"],
                "actor_b_name":   b["name"],
                "score":          round(score, 6),
                "corpus_a_items": a["corpus_size"],
                "corpus_b_items": b["corpus_size"],
            }

            if not dry_run:
                _upsert_resonance(conn, aid, bid, score, features)

            if score >= graph_thresh:
                above_graph += 1
                if not dry_run:
                    _upsert_graph_edge(conn, aid, bid, score)

            log.debug(
                "MATCH  %-28s ↔ %-28s  score=%.4f%s",
                a["name"][:28], b["name"][:28], score,
                "  [GRAPH]" if score >= graph_thresh else "",
            )

        # Progress log every 10 outer iterations
        if (i + 1) % 10 == 0 or i == n - 1:
            log.info(
                "  Progress: %d/%d outer loops | compared=%d | "
                "resonance=%d | graph=%d",
                i + 1, n, compared, above_res, above_graph,
            )

    if not dry_run:
        conn.commit()

    return {
        "pairs_compared": compared,
        "above_resonance_threshold": above_res,
        "above_graph_threshold":     above_graph,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — C-SOCINT Community Detection
# ─────────────────────────────────────────────────────────────────────────────

def _run_socint_communities(
    conn:      sqlite3.Connection,
    threshold: float,
    dry_run:   bool,
) -> dict:
    """
    Build a stylometric subgraph from socint_resonance rows above threshold
    and assign community IDs via greedy modularity communities.

    Writes community_id_socint to actor_network_metrics (UPSERT).
    Actors not in any community get community_id_socint = NULL.

    Returns { "communities_found": int, "actors_clustered": int }
    """
    try:
        import networkx as nx
        from networkx.algorithms.community import greedy_modularity_communities
    except ImportError:
        log.warning("NetworkX not available — C-SOCINT community pass skipped")
        return {"communities_found": 0, "actors_clustered": 0}

    # Load edges above threshold from socint_resonance
    edges = conn.execute(
        "SELECT actor_a, actor_b, score FROM socint_resonance WHERE score >= ?",
        (threshold,),
    ).fetchall()

    if not edges:
        log.info("No socint_resonance edges above %.2f — community pass skipped", threshold)
        return {"communities_found": 0, "actors_clustered": 0}

    G = nx.Graph()
    for row in edges:
        G.add_edge(row["actor_a"], row["actor_b"], weight=float(row["score"]))

    log.info(
        "C-SOCINT subgraph: %d nodes, %d edges",
        G.number_of_nodes(), G.number_of_edges(),
    )

    community_map: dict[int, int] = {}
    try:
        if nx.is_connected(G):
            communities = greedy_modularity_communities(G, weight="weight")
        else:
            communities = greedy_modularity_communities(G, weight="weight")
        for cid, members in enumerate(communities):
            for actor_id in members:
                community_map[actor_id] = cid
    except Exception as exc:
        log.warning("C-SOCINT community detection failed: %s", exc)
        return {"communities_found": 0, "actors_clustered": 0}

    n_communities = len(set(community_map.values()))
    n_clustered   = len(community_map)
    log.info(
        "C-SOCINT communities: %d  |  actors clustered: %d",
        n_communities, n_clustered,
    )

    if not dry_run and community_map:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for actor_id, cid in community_map.items():
            conn.execute(
                """
                INSERT INTO actor_network_metrics
                    (actor_id, betweenness, eigenvector, pagerank,
                     community_id, node_count, edge_count,
                     influence_score, community_id_socint, computed_at)
                VALUES (?, 0, 0, 0, NULL, 0, 0, 0, ?, ?)
                ON CONFLICT(actor_id) DO UPDATE SET
                    community_id_socint = excluded.community_id_socint,
                    computed_at         = excluded.computed_at
                """,
                (actor_id, cid, now),
            )
        conn.commit()

    return {
        "communities_found": n_communities,
        "actors_clustered":  n_clustered,
        "community_map":     community_map,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(
    db_path:        Path  = DB_PATH,
    dry_run:        bool  = False,
    threshold:      float = RESONANCE_THRESHOLD,
    graph_threshold: float = GRAPH_INJECT_THRESHOLD,
) -> dict:
    """
    Execute all three phases of the resonance batch engine.

    Parameters
    ----------
    db_path         : Path to database.db
    dry_run         : If True, compute but do not write to DB
    threshold       : Minimum score for socint_resonance entry (default 0.65)
    graph_threshold : Minimum score for entity_relationships injection (default 0.70)

    Returns
    -------
    dict with full summary for telemetry logging.
    """
    _t0 = time.monotonic()

    if not db_path.exists():
        log.error("Database not found: %s", db_path)
        return {"status": "error", "reason": "database not found"}

    log.info("Database  : %s", db_path)
    log.info("Dry run   : %s", dry_run)
    log.info("Threshold : resonance=%.2f  graph=%.2f", threshold, graph_threshold)

    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Ensure community_id_socint column exists
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(actor_network_metrics)")}
    if "community_id_socint" not in existing_cols:
        log.info("Adding actor_network_metrics.community_id_socint ...")
        conn.execute(
            "ALTER TABLE actor_network_metrics "
            "ADD COLUMN community_id_socint INTEGER DEFAULT NULL"
        )
        conn.commit()

    try:
        # ── Phase 1 ──────────────────────────────────────────────────────────
        log.info("=== Phase 1: Loading actor corpora ===")
        actors = _load_actor_fingerprints(conn)

        if len(actors) < 2:
            log.info("Fewer than 2 corpus-ready actors — nothing to compare.")
            conn.close()
            return {
                "status": "skipped",
                "reason": "insufficient corpus-ready actors",
                "actors_ready": len(actors),
            }

        # ── Phase 2 ──────────────────────────────────────────────────────────
        log.info("=== Phase 2: Pairwise resonance comparison ===")
        pair_stats = _run_pairwise(
            actors, conn, threshold, graph_threshold, dry_run
        )

        # ── Phase 3 ──────────────────────────────────────────────────────────
        log.info("=== Phase 3: C-SOCINT community detection ===")
        community_stats = _run_socint_communities(conn, threshold, dry_run)

    finally:
        conn.close()

    elapsed = time.monotonic() - _t0
    summary = {
        "status":              "done",
        "dry_run":             dry_run,
        "actors_ready":        len(actors),
        "resonance_threshold": threshold,
        "graph_threshold":     graph_threshold,
        "duration_s":          round(elapsed, 2),
        **pair_stats,
        **{f"csocint_{k}": v for k, v in community_stats.items()
           if k != "community_map"},
    }
    log.info("Complete: %s", {k: v for k, v in summary.items() if k != "community_map"})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE FLUX — Resonance Batch Engine"
    )
    parser.add_argument("--dry-run",         action="store_true")
    parser.add_argument("--threshold",  type=float, default=RESONANCE_THRESHOLD)
    parser.add_argument("--graph-threshold", type=float, default=GRAPH_INJECT_THRESHOLD)
    parser.add_argument("--db",         type=Path,  default=DB_PATH)
    args = parser.parse_args()

    result = run(
        db_path=args.db,
        dry_run=args.dry_run,
        threshold=args.threshold,
        graph_threshold=args.graph_threshold,
    )
    sys.exit(0 if result.get("status") in ("done", "skipped") else 1)

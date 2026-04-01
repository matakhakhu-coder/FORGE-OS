"""
FORGE — Coalition Detector Engine  (forge_modules/coalition_detector/engine.py)
================================================================================
Identifies recurring actor networks by counting how often actors appear
together across events.

WHY THIS IS DISTINCT FROM graph_engine COMMUNITY DETECTION
───────────────────────────────────────────────────────────
graph_engine runs Clauset-Newman-Moore greedy modularity optimisation on the
co-occurrence graph and writes results to actor_network_metrics.community_id.
That is a structural algorithm — it finds communities by optimising graph
modularity, regardless of raw count thresholds.

This engine uses a simpler, evidence-based approach:
  - Count exact event co-occurrences between every actor pair
  - If pair_count >= threshold (default 5), they are classified as a coalition
  - Coalitions are stored in actor_coalitions (separate table)
  - actor_network_metrics.community_id is NOT touched — no conflict

The two approaches are complementary and can be run independently.

ALGORITHM
─────────
1. Load all actor→event links from actor_events UNION event_actors
   (same dual-source approach as graph_engine to catch pipeline-generated links)
2. For each event, collect the set of actors present
3. Count pair co-occurrences across all events (actor_a, actor_b, count)
4. Filter pairs where count >= threshold
5. Union-Find grouping: connected pairs above threshold form one coalition
6. Write coalitions to actor_coalitions table
7. Update actor_network_metrics.community_id ONLY for actors whose
   existing community_id is NULL — never overwrite graph_engine results

DATA MODEL
──────────
actor_coalitions
  coalition_id    INTEGER PK AUTOINCREMENT
  actor_id        INTEGER FK → actors
  coalition_label TEXT     (e.g. "COALITION_3")
  co_occurrence   INTEGER  (max pair count for this actor within coalition)
  member_count    INTEGER  (total members in this coalition)
  threshold_used  INTEGER  (the N value used when this was computed)
  computed_at     TEXT

Returns a result dict for pipeline_runs logging.
"""

from __future__ import annotations

import sqlite3
import time
import logging
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger("forge.modules.coalition_detector")

DB_PATH = Path(__file__).resolve().parents[2] / "database.db"

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS actor_coalitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id        INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    coalition_label TEXT    NOT NULL,
    co_occurrence   INTEGER NOT NULL DEFAULT 0,
    member_count    INTEGER NOT NULL DEFAULT 1,
    threshold_used  INTEGER NOT NULL DEFAULT 5,
    computed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (actor_id, coalition_label)
);
CREATE INDEX IF NOT EXISTS idx_actor_coalitions_actor
    ON actor_coalitions (actor_id);
CREATE INDEX IF NOT EXISTS idx_actor_coalitions_label
    ON actor_coalitions (coalition_label);
"""


# ── Union-Find ────────────────────────────────────────────────────────────────

class _UnionFind:
    def __init__(self):
        self._parent: dict = {}

    def find(self, x):
        self._parent.setdefault(x, x)
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x, y):
        self._parent[self.find(x)] = self.find(y)

    def groups(self) -> dict[int, list]:
        """Return {root: [members]} for all nodes."""
        result: dict = defaultdict(list)
        for node in self._parent:
            result[self.find(node)].append(node)
        return dict(result)


# ── Main engine function ──────────────────────────────────────────────────────

def run(signal: dict = None, threshold: int = 5,
        db_path: Path = None) -> dict:
    """
    Public engine entry point.

    Called by:
      - Conclave hook (signal=dict, threshold=5) — analyses full graph
        on every ingest cycle (lightweight — SQL is the heavy lift)
      - Control Room via /api/control/run_coalition_detector
      - module.register() engine registration

    signal argument is accepted for Conclave hook compatibility but is
    not used — the engine always analyses the full actor graph, not one
    signal at a time. This is intentional: coalition membership only
    becomes meaningful in aggregate.

    Returns pipeline_runs-compatible result dict.
    """
    _db = db_path or DB_PATH
    start = time.monotonic()

    conn = _open_db(_db)
    try:
        _ensure_schema(conn)

        # ── 1. Load actor→event links (both manual and pipeline-generated) ──
        # Check which tables exist before querying
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        if "event_actors" in tables:
            rows = conn.execute("""
                SELECT DISTINCT actor_id, event_id FROM (
                    SELECT actor_id, event_id FROM actor_events
                    UNION ALL
                    SELECT actor_id, event_id FROM event_actors
                )
            """).fetchall()
        else:
            rows = conn.execute(
                "SELECT actor_id, event_id FROM actor_events"
            ).fetchall()

        if not rows:
            log.info("[coalition_detector] No actor-event links found — skipping")
            conn.close()
            return {
                "status":      "success",
                "coalitions":  0,
                "actors_tagged": 0,
                "pairs_above_threshold": 0,
                "threshold":   threshold,
                "duration_s":  round(time.monotonic() - start, 2),
            }

        # ── 2. Build event→actors map ───────────────────────────────────────
        event_actors: dict[int, set] = defaultdict(set)
        for row in rows:
            event_actors[row["event_id"]].add(row["actor_id"])

        # ── 3. Count pair co-occurrences ────────────────────────────────────
        pair_counts: dict[tuple, int] = defaultdict(int)
        for actors_in_event in event_actors.values():
            actor_list = sorted(actors_in_event)
            for a, b in combinations(actor_list, 2):
                pair_counts[(a, b)] += 1

        # ── 4. Filter pairs above threshold ─────────────────────────────────
        qualifying = {
            pair: count
            for pair, count in pair_counts.items()
            if count >= threshold
        }

        # Filter to only actor_ids that exist in actors table
        valid_actors = {
            r[0] for r in conn.execute(
                "SELECT actor_id FROM actors"
            ).fetchall()
        }

        # Rebuild qualifying with only valid actors
        qualifying = {
            (a, b): count
            for (a, b), count in qualifying.items()
            if a in valid_actors and b in valid_actors
        }

        log.info(f"[coalition_detector] {len(qualifying)} pairs after actor validation")
        log.info(
            f"[coalition_detector] {len(pair_counts)} pairs found, "
            f"{len(qualifying)} above threshold={threshold}"
        )

        if not qualifying:
            # Nothing to group — clear stale coalitions and exit
            conn.execute("DELETE FROM actor_coalitions")
            conn.commit()
            conn.close()
            return {
                "status":      "success",
                "coalitions":  0,
                "actors_tagged": 0,
                "pairs_above_threshold": 0,
                "threshold":   threshold,
                "duration_s":  round(time.monotonic() - start, 2),
            }

        # ── 5. Union-Find grouping ───────────────────────────────────────────
        uf = _UnionFind()
        for (a, b) in qualifying:
            uf.union(a, b)

        groups = uf.groups()

        # Only keep groups with 2+ members (singletons can't be coalitions)
        coalitions = {
            root: members
            for root, members in groups.items()
            if len(members) >= 2
        }

        log.info(
            f"[coalition_detector] {len(coalitions)} coalition(s) detected "
            f"({sum(len(m) for m in coalitions.values())} actors)"
        )

        # ── 6. Build per-actor max co-occurrence score ───────────────────────
        # For each actor, find the highest pair count they appear in
        actor_max_cooc: dict[int, int] = defaultdict(int)
        for (a, b), count in qualifying.items():
            actor_max_cooc[a] = max(actor_max_cooc[a], count)
            actor_max_cooc[b] = max(actor_max_cooc[b], count)

        # ── 7. Write to actor_coalitions ─────────────────────────────────────
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Full replace — coalitions are recomputed fresh each run
        conn.execute("DELETE FROM actor_coalitions")

        actors_tagged = 0
        for coalition_idx, (root, members) in enumerate(coalitions.items()):
            label        = f"COALITION_{coalition_idx + 1}"
            member_count = len(members)
            for actor_id in members:
                co_occ = actor_max_cooc.get(actor_id, threshold)
                conn.execute("""
                    INSERT OR IGNORE INTO actor_coalitions
                        (actor_id, coalition_label, co_occurrence,
                         member_count, threshold_used, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (actor_id, label, co_occ, member_count, threshold, now))
                actors_tagged += 1

        # ── 8. Update actor_network_metrics.community_id ─────────────────────
        # ONLY for actors whose community_id is currently NULL.
        # graph_engine's CNM results are preserved — we never overwrite.
        for coalition_idx, (root, members) in enumerate(coalitions.items()):
            for actor_id in members:
                conn.execute("""
                    UPDATE actor_network_metrics
                    SET    community_id = ?
                    WHERE  actor_id     = ?
                      AND  community_id IS NULL
                """, (coalition_idx + 1000, actor_id))
                # +1000 offset distinguishes coalition IDs from CNM IDs (0-based)

        conn.commit()

        duration = round(time.monotonic() - start, 2)
        log.info(
            f"[coalition_detector] Done — {len(coalitions)} coalitions, "
            f"{actors_tagged} actors tagged in {duration}s"
        )

        return {
            "status":                "success",
            "coalitions":            len(coalitions),
            "actors_tagged":         actors_tagged,
            "pairs_above_threshold": len(qualifying),
            "threshold":             threshold,
            "duration_s":            duration,
        }

    except Exception as exc:
        log.error(f"[coalition_detector] Engine error: {exc}")
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return {
            "status":    "error",
            "error":     str(exc),
            "threshold": threshold,
            "duration_s": round(time.monotonic() - start, 2),
        }
    finally:
        conn.close()


# ── Query helper (used by API route) ─────────────────────────────────────────

def query_coalitions(db_path: Path = None) -> list[dict]:
    """
    Return all detected coalitions with their member actor details.
    Used by GET /api/graph/coalitions.
    """
    _db = db_path or DB_PATH
    conn = _open_db(_db)
    try:
        _ensure_schema(conn)
        rows = conn.execute("""
            SELECT
                ac.coalition_label,
                ac.member_count,
                ac.threshold_used,
                ac.computed_at,
                ac.actor_id,
                a.name        AS actor_name,
                a.type        AS actor_type,
                ac.co_occurrence,
                anm.pagerank,
                anm.influence_score
            FROM   actor_coalitions ac
            JOIN   actors a   ON a.actor_id   = ac.actor_id
            LEFT JOIN actor_network_metrics anm
                              ON anm.actor_id = ac.actor_id
            ORDER  BY ac.coalition_label, ac.co_occurrence DESC
        """).fetchall()
    finally:
        conn.close()

    # Group by coalition_label
    coalitions: dict[str, dict] = {}
    for r in rows:
        label = r["coalition_label"]
        if label not in coalitions:
            coalitions[label] = {
                "coalition_label": label,
                "member_count":    r["member_count"],
                "threshold_used":  r["threshold_used"],
                "computed_at":     r["computed_at"],
                "members":         [],
            }
        coalitions[label]["members"].append({
            "actor_id":       r["actor_id"],
            "actor_name":     r["actor_name"],
            "actor_type":     r["actor_type"],
            "co_occurrence":  r["co_occurrence"],
            "pagerank":       r["pagerank"],
            "influence_score": r["influence_score"],
        })

    return list(coalitions.values())


# ── Internals ─────────────────────────────────────────────────────────────────

def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
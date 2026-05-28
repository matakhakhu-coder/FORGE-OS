#!/usr/bin/env python3
"""
FORGE — Graph Intelligence Engine  (Phase 21 → 24)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Phase 21 (original): actor_events co-occurrence graph →
  Betweenness, Eigenvector, PageRank, Community Detection.

Phase 24 — The Centrality Protocol: multi-modal Global Influence Score.

  Factor 1 — Direct Links        (weight 0.40)
    Betweenness centrality from actor_events co-occurrence.
    High = broker / courier between otherwise separate clusters.

  Factor 2 — Named Relationships (weight 0.35)
    PageRank on the entity_relationships directed graph,
    weighted by analyst-assigned confidence.

  Factor 3 — Signal Proximity    (weight 0.25)
    Actors whose linked events are geographically near high-scoring
    correlated_incidents pairs accumulate a proximity boost.

  influence_score = 0.40 × betwn_norm
                  + 0.35 × rel_pagerank_norm
                  + 0.25 × proximity_norm

All three components normalised independently to [0,1] before combining.
Original betweenness/eigenvector/pagerank metrics written unchanged.
influence_score is a new column added by Phase 24.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
# P1-07: standard import — importlib.spec_from_file_location removed
try:
    from forage.utils.pipeline_logger import log_run
except ImportError:
    def log_run(*args, **kwargs):  # type: ignore[misc]
        pass  # logging must never crash the pipeline
from typing import Optional

REPORT_TOP_N      = 10
MIN_CONNECTIONS   = 1
W_BETWEENNESS     = 0.40
W_RELATIONSHIP    = 0.35
W_PROXIMITY       = 0.25
PROXIMITY_MAX_KM  = 100.0


def _resolve_db(override: Optional[str] = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [graph_engine] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [graph_engine] WARN  {msg}",
                                   file=sys.stderr, flush=True)


def _pagerank(G, weight: str = "weight", alpha: float = 0.85,
              max_iter: int = 100, tol: float = 1.0e-4) -> dict:
    """
    Pure-Python power-iteration PageRank.
    Drop-in replacement for nx.pagerank() — avoids BLAS/NumPy hangs seen in
    NetworkX 3.6 + NumPy 2.4 on Windows.
    Works on both nx.Graph and nx.DiGraph.
    """
    nodes = list(G.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    idx   = {v: i for i, v in enumerate(nodes)}
    # Build row-normalised adjacency list (out-edges for directed, all edges for undirected)
    out_w: list = [[] for _ in range(n)]  # [(target_idx, weight), ...]
    out_sum: list = [0.0] * n
    is_directed = G.is_directed()
    for u, v, d in G.edges(data=True):
        w = float(d.get(weight, 1.0))
        iu, iv = idx[u], idx[v]
        out_w[iu].append((iv, w))
        out_sum[iu] += w
        if not is_directed:
            out_w[iv].append((iu, w))
            out_sum[iv] += w
    # Initialise uniform distribution
    rank = [1.0 / n] * n
    for _ in range(max_iter):
        new_rank = [0.0] * n
        dangling_sum = 0.0
        for i in range(n):
            if out_sum[i] == 0.0:          # dangling node
                dangling_sum += rank[i]
            else:
                for j, w in out_w[i]:
                    new_rank[j] += alpha * rank[i] * (w / out_sum[i])
        # Distribute dangling mass and (1-alpha) teleportation uniformly
        base = (alpha * dangling_sum + (1.0 - alpha)) / n
        for i in range(n):
            new_rank[i] += base
        # Check convergence
        err = sum(abs(new_rank[i] - rank[i]) for i in range(n))
        rank = new_rank
        if err < n * tol:
            break
    total = sum(rank)
    return {nodes[i]: rank[i] / total for i in range(n)}


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R    = 6_371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a    = (math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _normalise(d: dict) -> dict:
    if not d:
        return d
    lo  = min(d.values())
    hi  = max(d.values())
    rng = hi - lo
    if rng == 0:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / rng for k, v in d.items()}


class GraphEngine:

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _resolve_db()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS actor_network_metrics (
                actor_id        INTEGER PRIMARY KEY
                                REFERENCES actors(actor_id) ON DELETE CASCADE,
                betweenness     REAL    NOT NULL DEFAULT 0,
                eigenvector     REAL    NOT NULL DEFAULT 0,
                pagerank        REAL    NOT NULL DEFAULT 0,
                community_id    INTEGER,
                node_count      INTEGER,
                edge_count      INTEGER,
                influence_score REAL    NOT NULL DEFAULT 0,
                computed_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(actor_network_metrics)")}
        if "influence_score" not in existing:
            conn.execute(
                "ALTER TABLE actor_network_metrics "
                "ADD COLUMN influence_score REAL NOT NULL DEFAULT 0"
            )
        if "community_id_socint" not in existing:
            conn.execute(
                "ALTER TABLE actor_network_metrics "
                "ADD COLUMN community_id_socint INTEGER DEFAULT NULL"
            )
        conn.commit()

    def _load_cooccurrence_edges(self, conn: sqlite3.Connection) -> tuple[list, list]:
        actors = conn.execute(
            "SELECT actor_id, name, type FROM actors ORDER BY actor_id"
        ).fetchall()
        if len(actors) < 2:
            return actors, []
        raw = conn.execute("""
            WITH all_actor_events AS (
                SELECT actor_id, event_id FROM actor_events
                UNION
                SELECT actor_id, event_id FROM event_actors
            )
            SELECT ae1.actor_id AS a,
                   ae2.actor_id AS b,
                   COUNT(DISTINCT ae1.event_id) AS shared
            FROM   all_actor_events ae1
            JOIN   all_actor_events ae2
                ON ae2.event_id  = ae1.event_id
               AND ae2.actor_id > ae1.actor_id
            GROUP  BY ae1.actor_id, ae2.actor_id
            HAVING COUNT(DISTINCT ae1.event_id) >= ?
        """, (MIN_CONNECTIONS,)).fetchall()
        return actors, raw

    def _load_relationship_edges(self, conn: sqlite3.Connection) -> list:
        try:
            return conn.execute(
                "SELECT subject_actor_id AS a, object_actor_id AS b, "
                "confidence AS weight FROM entity_relationships "
                "WHERE relation_type != 'co_occurrence'"
            ).fetchall()
        except Exception:
            return []

    def _compute_relationship_pagerank(self, actors: list, rel_edges: list) -> dict:
        if not rel_edges:
            return {}
        try:
            import networkx as nx
        except ImportError:
            return {}
        G = nx.DiGraph()
        for actor in actors:
            G.add_node(actor["actor_id"])
        for row in rel_edges:
            G.add_edge(row["a"], row["b"], weight=float(row["weight"]))
        if G.number_of_edges() == 0:
            return {}
        try:
            return _pagerank(G, weight="weight", alpha=0.85, max_iter=200)
        except Exception as exc:
            warn(f"Relationship PageRank failed: {exc}")
            return {}

    def _load_correlated_pairs(self, conn: sqlite3.Connection) -> list:
        try:
            return conn.execute(
                "SELECT ci.correlation_score, "
                "sa.lat AS lat_a, sa.lng AS lng_a, "
                "sb.lat AS lat_b, sb.lng AS lng_b "
                "FROM correlated_incidents ci "
                "JOIN signals sa ON sa.signal_id = ci.signal_a "
                "JOIN signals sb ON sb.signal_id = ci.signal_b "
                "WHERE ci.correlation_score >= 0.7 "
                "  AND sa.lat IS NOT NULL AND sb.lat IS NOT NULL "
                "ORDER BY ci.correlation_score DESC LIMIT 500"
            ).fetchall()
        except Exception:
            return []

    def _load_actor_event_coords(self, conn: sqlite3.Connection) -> dict:
        try:
            rows = conn.execute(
                "SELECT ae.actor_id, e.latitude, e.longitude "
                "FROM (SELECT actor_id, event_id FROM actor_events "
                "      UNION SELECT actor_id, event_id FROM event_actors) ae "
                "JOIN events e ON e.event_id = ae.event_id "
                "WHERE e.latitude IS NOT NULL AND e.longitude IS NOT NULL"
            ).fetchall()
        except Exception:
            return {}
        coords: dict = {}
        for r in rows:
            coords.setdefault(r["actor_id"], []).append((r["latitude"], r["longitude"]))
        return coords

    def _compute_proximity_scores(self, actors: list, corr_pairs: list,
                                   actor_coords: dict) -> dict:
        scores: dict = {actor["actor_id"]: 0.0 for actor in actors}
        if not corr_pairs or not actor_coords:
            return scores
        for actor in actors:
            aid  = actor["actor_id"]
            locs = actor_coords.get(aid, [])
            if not locs:
                continue
            total = 0.0
            for pair in corr_pairs:
                for (alat, alng) in locs:
                    try:
                        d1 = _haversine_km(alat, alng, pair["lat_a"], pair["lng_a"])
                        d2 = _haversine_km(alat, alng, pair["lat_b"], pair["lng_b"])
                        min_d = min(d1, d2)
                    except Exception:
                        continue
                    if min_d <= PROXIMITY_MAX_KM:
                        total += pair["correlation_score"] * (1.0 - min_d / PROXIMITY_MAX_KM)
            scores[aid] = total
        return scores

    def _build_graph(self, actors: list, edges: list):
        import networkx as nx
        G = nx.Graph()
        for actor in actors:
            G.add_node(actor["actor_id"], name=actor["name"], actor_type=actor["type"])
        for row in edges:
            G.add_edge(row["a"], row["b"], weight=row["shared"])
        log(f"Co-occurrence graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        return G

    def _compute_core_metrics(self, G) -> dict:
        import networkx as nx
        from networkx.algorithms.community import greedy_modularity_communities
        results: dict = {}
        n = G.number_of_nodes()
        if n == 0:
            return results

        log("Computing betweenness centrality...")
        betweenness = nx.betweenness_centrality(G, weight="weight", normalized=True)
        log("Computing PageRank (co-occurrence graph)...")
        pagerank = _pagerank(G, weight="weight", alpha=0.85, max_iter=100)
        log("Computing eigenvector centrality...")
        try:
            eigenvector = nx.eigenvector_centrality_numpy(G, weight="weight")
        except Exception:
            warn("Eigenvector centrality failed — using degree centrality")
            eigenvector = nx.degree_centrality(G)

        log("Running community detection (Clauset-Newman-Moore)...")
        community_map: dict = {}
        try:
            if nx.is_connected(G):
                communities = greedy_modularity_communities(G, weight="weight")
            else:
                largest_cc  = max(nx.connected_components(G), key=len)
                sub         = G.subgraph(largest_cc)
                communities = greedy_modularity_communities(sub, weight="weight")
            for cid, community in enumerate(communities):
                for node in community:
                    community_map[node] = cid
        except Exception as exc:
            warn(f"Community detection failed: {exc}")

        for node in G.nodes():
            results[node] = {
                "betweenness":  round(betweenness.get(node, 0.0), 6),
                "eigenvector":  round(eigenvector.get(node, 0.0), 6),
                "pagerank":     round(pagerank.get(node, 0.0),    6),
                "community_id": community_map.get(node),
                "node_count":   n,
                "edge_count":   G.number_of_edges(),
            }
        return results

    def _compute_influence_scores(self, core_metrics: dict,
                                   rel_pagerank: dict, proximity_raw: dict) -> dict:
        betwn_raw = {aid: m["betweenness"] for aid, m in core_metrics.items()}
        betwn_n   = _normalise(betwn_raw)
        rel_n     = _normalise(dict(rel_pagerank))
        prox_n    = _normalise(dict(proximity_raw))
        scores: dict = {}
        for aid in core_metrics:
            b = betwn_n.get(aid, 0.0)
            r = rel_n.get(aid,   0.0)
            p = prox_n.get(aid,  0.0)
            scores[aid] = round(
                W_BETWEENNESS * b + W_RELATIONSHIP * r + W_PROXIMITY * p, 6
            )
        return scores

    def _write_metrics(self, conn: sqlite3.Connection, core_metrics: dict,
                       influence: dict, dry_run: bool = False) -> int:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        written = 0
        skipped = 0
        for actor_id, m in core_metrics.items():
            if dry_run:
                continue
            try:
                conn.execute("""
                    INSERT INTO actor_network_metrics
                        (actor_id, betweenness, eigenvector, pagerank,
                         community_id, node_count, edge_count,
                         influence_score, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(actor_id) DO UPDATE SET
                        betweenness     = excluded.betweenness,
                        eigenvector     = excluded.eigenvector,
                        pagerank        = excluded.pagerank,
                        community_id    = excluded.community_id,
                        node_count      = excluded.node_count,
                        edge_count      = excluded.edge_count,
                        influence_score = excluded.influence_score,
                        computed_at     = excluded.computed_at
                """, (
                    actor_id, m["betweenness"], m["eigenvector"], m["pagerank"],
                    m["community_id"], m["node_count"], m["edge_count"],
                    influence.get(actor_id, 0.0), now,
                ))
                written += 1
            except Exception:
                # Orphaned actor_id (deleted actor still referenced in event_actors)
                skipped += 1
        if not dry_run:
            conn.commit()
            if skipped:
                import logging as _log
                _log.getLogger("forge.graph_engine").debug(
                    f"[graph_engine] _write_metrics: skipped {skipped} orphaned actor_ids"
                )
        return written

    def _compute_socint_communities(self, conn: sqlite3.Connection,
                                        dry_run: bool = False) -> dict:
        """
        C-SOCINT pass — community detection on the stylometric_match subgraph.

        Loads only edges with relation_type='stylometric_match' from
        entity_relationships (written by flux/processors/resonance.py when
        resonance_score >= GRAPH_INJECT_THRESHOLD=0.70).

        Runs greedy_modularity_communities on that undirected subgraph and
        writes the resulting community IDs to actor_network_metrics.community_id_socint.
        The main community_id column (co-occurrence graph) is never touched.
        """
        try:
            import networkx as nx
            from networkx.algorithms.community import greedy_modularity_communities
        except ImportError:
            warn("NetworkX not available — C-SOCINT pass skipped")
            return {"skipped": True, "reason": "networkx_missing"}

        try:
            rows = conn.execute(
                "SELECT subject_actor_id AS a, object_actor_id AS b, "
                "confidence AS weight "
                "FROM entity_relationships "
                "WHERE relation_type = 'stylometric_match'"
            ).fetchall()
        except Exception as exc:
            warn(f"C-SOCINT: could not load stylometric_match edges: {exc}")
            return {"skipped": True, "reason": str(exc)}

        if not rows:
            log("C-SOCINT: no stylometric_match edges — pass skipped")
            return {"edges": 0, "communities": 0}

        G = nx.Graph()
        for row in rows:
            G.add_edge(row["a"], row["b"], weight=float(row["weight"]))

        log(f"C-SOCINT graph: {G.number_of_nodes()} nodes, "
            f"{G.number_of_edges()} edges")

        community_map: dict = {}
        try:
            if nx.is_connected(G):
                communities = greedy_modularity_communities(G, weight="weight")
            else:
                largest_cc  = max(nx.connected_components(G), key=len)
                sub         = G.subgraph(largest_cc)
                communities = greedy_modularity_communities(sub, weight="weight")
            for cid, community in enumerate(communities):
                for node in community:
                    community_map[node] = cid
        except Exception as exc:
            warn(f"C-SOCINT community detection failed: {exc}")
            return {"edges": G.number_of_edges(), "communities": 0,
                    "error": str(exc)}

        n_communities = len(set(community_map.values()))
        log(f"C-SOCINT: {n_communities} communities across "
            f"{len(community_map)} actors")

        if not dry_run:
            for actor_id, cid in community_map.items():
                try:
                    conn.execute(
                        "INSERT INTO actor_network_metrics "
                        "    (actor_id, community_id_socint) "
                        "VALUES (?, ?) "
                        "ON CONFLICT(actor_id) DO UPDATE SET "
                        "    community_id_socint = excluded.community_id_socint",
                        (actor_id, cid),
                    )
                except Exception as exc:
                    warn(f"C-SOCINT write failed for actor {actor_id}: {exc}")
            conn.commit()

        return {
            "edges":       G.number_of_edges(),
            "communities": n_communities,
            "written":     len(community_map),
            "dry_run":     dry_run,
        }

    def run(self, dry_run: bool = False) -> dict:
        _t0 = __import__("time").monotonic()
        log(f"Database : {self._db_path}")
        log(f"Dry run  : {dry_run}")
        conn = _open_db(self._db_path)
        self._ensure_schema(conn)

        actors, cooc_edges = self._load_cooccurrence_edges(conn)
        log(f"Actors: {len(actors)} | Co-occurrence edges: {len(cooc_edges)}")
        if len(actors) == 0:
            conn.close()
            return {"status": "empty", "actors": 0, "edges": 0, "communities": 0}
        if len(actors) == 1:
            conn.close()
            return {"status": "too_few_actors", "actors": 1, "edges": 0}

        G            = self._build_graph(actors, cooc_edges)
        core_metrics = self._compute_core_metrics(G)

        rel_edges    = self._load_relationship_edges(conn)
        log(f"Named relationship edges: {len(rel_edges)}")
        rel_pagerank = self._compute_relationship_pagerank(actors, rel_edges)

        corr_pairs   = self._load_correlated_pairs(conn)
        actor_coords = self._load_actor_event_coords(conn)
        log(f"Correlated pairs: {len(corr_pairs)} | Actors with coords: {len(actor_coords)}")
        proximity = self._compute_proximity_scores(actors, corr_pairs, actor_coords)

        log("Computing Global Influence Scores...")
        influence = self._compute_influence_scores(core_metrics, rel_pagerank, proximity)

        n_communities = len({m["community_id"] for m in core_metrics.values()
                             if m["community_id"] is not None})

        written = self._write_metrics(conn, core_metrics, influence, dry_run=dry_run)

        log("Running C-SOCINT community pass (stylometric_match subgraph)...")
        socint_result = self._compute_socint_communities(conn, dry_run=dry_run)

        conn.close()

        summary = {
            "status": "done", "actors": len(actors),
            "edges": G.number_of_edges(), "rel_edges": len(rel_edges),
            "corr_pairs": len(corr_pairs), "communities": n_communities,
            "written": written, "dry_run": dry_run,
            "socint_communities": socint_result.get("communities", 0),
            "socint_edges":       socint_result.get("edges", 0),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        log(f"Complete: {summary}")
        log_run(self._db_path, "graph_engine", "success",
                records_in=summary.get("actors", 0),
                records_out=summary.get("written", 0),
                duration_s=__import__("time").monotonic() - _t0,
                detail=summary)
        return summary

    def report(self) -> None:
        conn = _open_db(self._db_path)
        try:
            rows = conn.execute("""
                SELECT a.name, a.type,
                       m.influence_score, m.betweenness,
                       m.eigenvector, m.pagerank, m.community_id
                FROM   actor_network_metrics m
                JOIN   actors a ON a.actor_id = m.actor_id
                ORDER  BY m.influence_score DESC
                LIMIT  ?
            """, (REPORT_TOP_N,)).fetchall()
            meta = conn.execute(
                "SELECT MAX(computed_at) AS ts, COUNT(*) AS n "
                "FROM actor_network_metrics"
            ).fetchone()
        except Exception as exc:
            print(f"Error reading metrics: {exc}")
            conn.close()
            return
        conn.close()
        if not rows:
            print("No metrics computed yet.")
            return
        print(f"\n{'-'*80}")
        print(f"  FORGE — Top {REPORT_TOP_N} Actors by Global Influence Score")
        print(f"  Computed: {meta['ts']}  |  Total actors: {meta['n']}")
        print(f"{'-'*80}")
        print(f"  {'Actor':<28} {'Type':<14} {'Influence':>9} {'Betwn':>7} {'PRank':>7} {'Com':>4}")
        print(f"{'-'*80}")
        for r in rows:
            print(f"  {r['name']:<28} {r['type']:<14} "
                  f"{r['influence_score']:>9.4f} {r['betweenness']:>7.4f} "
                  f"{r['pagerank']:>7.4f} {str(r['community_id'] or '-'):>4}")
        print(f"{'-'*80}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Graph Intelligence Engine"
    )
    parser.add_argument("--db",          type=Path, default=None)
    parser.add_argument("--recalculate", action="store_true")
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--report",      action="store_true")
    args = parser.parse_args()

    engine = GraphEngine(db_path=_resolve_db(str(args.db) if args.db else None))
    if args.report and not args.recalculate:
        engine.report()
        sys.exit(0)
    result = engine.run(dry_run=args.dry_run)
    engine.report()
    sys.exit(0 if result.get("status") in ("done", "empty", "too_few_actors") else 1)

def build_graphs():
    print("[Graph Engine] Building graphs...")
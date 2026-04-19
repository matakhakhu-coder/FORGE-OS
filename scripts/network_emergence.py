"""
network_emergence.py — Phase 66: Coalition Matrix
==================================================
Native graph-traversal engine for the FORGE actor network.
Uses SQLite recursive CTEs to find shortest actor paths without exporting
the database to an external graph tool.

EDGE SURFACES TRAVERSED
  1. signal_actors  — implicit co-occurrence: Actor A and Actor B share a signal.
  2. entity_relationships — explicit typed edges: ACCUSED_OF, co_occurrence, osint_match, ...

HUB-POISONING DEFENCE
  Any intermediate node whose combined degree (signal_actors + entity_relationships)
  exceeds --hub-threshold is bypassed. Source and target nodes are always allowed
  regardless of degree.

USAGE
  python scripts/network_emergence.py --source 955 --target 39
  python scripts/network_emergence.py --source 955 --target 39 --max-hops 6 --hub-threshold 50
  python scripts/network_emergence.py --source 955 --target 39 --no-hub-filter
  python scripts/network_emergence.py --metrics --top 20
  python scripts/network_emergence.py --neighbours 955 --depth 2
"""

import sys
import os
import json
import time
import argparse
import textwrap
from pathlib import Path
from typing import Optional

# --- path bootstrap so we can import core.db from anywhere ---
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from core.db.connection import get_connection   # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_MAX_HOPS        = 4
DEFAULT_HUB_THRESHOLD   = 100    # nodes with combined degree > N are bypassed
VERY_LARGE              = 999999  # sentinel for "no hub filter"
LINE_WIDTH              = 76

# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _actor_name(conn, actor_id: int) -> str:
    cur = conn.execute("SELECT name FROM actors WHERE actor_id = ?", (actor_id,))
    row = cur.fetchone()
    return row["name"] if row else f"[unknown:{actor_id}]"


def _actor_info(conn, actor_id: int) -> dict:
    cur = conn.execute(
        "SELECT actor_id, name, type, gravity_score, confidence_score FROM actors WHERE actor_id = ?",
        (actor_id,),
    )
    row = cur.fetchone()
    if row:
        return dict(row)
    return {"actor_id": actor_id, "name": f"[unknown:{actor_id}]", "type": None,
            "gravity_score": None, "confidence_score": None}


def _combined_degree(conn, actor_id: int) -> int:
    """
    Returns the total number of edges touching actor_id across both surfaces:
    signal_actors + entity_relationships (both directions).
    """
    sa_deg = conn.execute(
        "SELECT COUNT(*) FROM signal_actors WHERE actor_id = ?", (actor_id,)
    ).fetchone()[0]
    er_deg = conn.execute(
        "SELECT COUNT(*) FROM entity_relationships "
        "WHERE subject_actor_id = ? OR object_actor_id = ?",
        (actor_id, actor_id),
    ).fetchone()[0]
    return sa_deg + er_deg


def _degree_table(conn) -> dict[int, int]:
    """Materialise combined degree for all actors in a single pass."""
    rows_sa = conn.execute(
        "SELECT actor_id, COUNT(*) AS d FROM signal_actors GROUP BY actor_id"
    ).fetchall()
    rows_er = conn.execute(
        "SELECT subject_actor_id AS actor_id, COUNT(*) AS d "
        "FROM entity_relationships GROUP BY subject_actor_id "
        "UNION ALL "
        "SELECT object_actor_id AS actor_id, COUNT(*) AS d "
        "FROM entity_relationships GROUP BY object_actor_id"
    ).fetchall()
    deg: dict[int, int] = {}
    for r in rows_sa:
        deg[r["actor_id"]] = deg.get(r["actor_id"], 0) + r["d"]
    for r in rows_er:
        deg[r["actor_id"]] = deg.get(r["actor_id"], 0) + r["d"]
    return deg


def _print_separator(char="-"):
    print(char * LINE_WIDTH)


def _print_header(title: str):
    _print_separator("=")
    print(f"  {title}")
    _print_separator("=")


# ─────────────────────────────────────────────────────────────────────────────
# CORE: find_shortest_path
# ─────────────────────────────────────────────────────────────────────────────
_PATH_SQL = """
WITH

  -- ── Combined degree per actor across both edge surfaces ──────────────────
  sa_deg AS (
    SELECT actor_id, COUNT(*) AS d FROM signal_actors GROUP BY actor_id
  ),
  er_deg AS (
    SELECT actor_id, SUM(d) AS d FROM (
      SELECT subject_actor_id AS actor_id, COUNT(*) AS d
        FROM entity_relationships GROUP BY subject_actor_id
      UNION ALL
      SELECT object_actor_id AS actor_id, COUNT(*) AS d
        FROM entity_relationships GROUP BY object_actor_id
    ) GROUP BY actor_id
  ),
  actor_degree AS (
    SELECT COALESCE(sa.actor_id, er.actor_id) AS actor_id,
           COALESCE(sa.d, 0) + COALESCE(er.d, 0) AS degree
    FROM sa_deg sa
    LEFT JOIN er_deg er ON er.actor_id = sa.actor_id
    UNION
    SELECT COALESCE(er.actor_id, sa.actor_id) AS actor_id,
           COALESCE(sa.d, 0) + COALESCE(er.d, 0) AS degree
    FROM er_deg er
    LEFT JOIN sa_deg sa ON sa.actor_id = er.actor_id
  ),

  -- ── Flattened edge list (bidirectional) ──────────────────────────────────
  -- Surface 1: shared-signal co-occurrence (undirected)
  signal_edges AS (
    SELECT sa1.actor_id AS src,
           sa2.actor_id AS dst,
           'signal:' || sa1.signal_id AS edge_label
    FROM signal_actors sa1
    JOIN signal_actors sa2
      ON sa2.signal_id = sa1.signal_id
     AND sa2.actor_id  <> sa1.actor_id
  ),
  -- Surface 2: typed entity relationships (bidirectional)
  rel_edges AS (
    SELECT subject_actor_id AS src,
           object_actor_id  AS dst,
           'rel:'  || relation_type || ':' || ROUND(confidence, 2) AS edge_label
    FROM entity_relationships
    UNION ALL
    SELECT object_actor_id  AS src,
           subject_actor_id AS dst,
           'rel[R]:' || relation_type || ':' || ROUND(confidence, 2) AS edge_label
    FROM entity_relationships
  ),
  -- Combined edge universe
  all_edges AS (
    SELECT src, dst, edge_label FROM signal_edges
    UNION ALL
    SELECT src, dst, edge_label FROM rel_edges
  ),

  -- ── BFS (FIFO queue via UNION ALL) ───────────────────────────────────────
  bfs (
    current_node,   -- actor_id at frontier
    path_ids,       -- CSV of actor_ids visited so far (for cycle detection)
    path_edges,     -- CSV of edge labels used (parallel to path_ids hops)
    hop_count       -- depth
  ) AS (

    -- Base case: start node
    SELECT
      CAST(:source AS INTEGER),
      CAST(:source AS TEXT),   -- path_ids starts with just source
      '',                       -- no edges yet
      0

    UNION ALL

    -- Recursive step: expand one hop
    SELECT
      e.dst,
      bfs.path_ids  || ',' || e.dst,
      CASE WHEN bfs.path_edges = ''
           THEN e.edge_label
           ELSE bfs.path_edges || '|' || e.edge_label
      END,
      bfs.hop_count + 1

    FROM bfs
    JOIN all_edges e ON e.src = bfs.current_node
    JOIN actor_degree ad ON ad.actor_id = e.dst

    WHERE
      -- Depth limit
      bfs.hop_count < :max_hops

      -- Cycle prevention: dst not already in path
      AND ',' || bfs.path_ids || ',' NOT LIKE '%,' || e.dst || ',%'
      AND bfs.path_ids NOT LIKE CAST(e.dst AS TEXT)

      -- Anti-hub filter: bypass high-degree intermediate nodes.
      -- Target is always allowed (we want to ARRIVE there).
      -- Source is always the starting point (already excluded by cycle check).
      AND (
        ad.degree <= :hub_threshold
        OR e.dst = :target
      )
  )

-- First (= shortest) path that reaches the target
SELECT
  path_ids,
  path_edges,
  hop_count
FROM bfs
WHERE current_node = :target
ORDER BY hop_count
LIMIT 1
"""


def find_shortest_path(
    source_actor_id: int,
    target_actor_id: int,
    max_hops: int = DEFAULT_MAX_HOPS,
    hub_degree_threshold: int = DEFAULT_HUB_THRESHOLD,
) -> Optional[dict]:
    """
    Find the shortest actor path from source to target.

    Returns a dict:
      {
        "hops": int,
        "path": [{"actor_id": int, "name": str, "degree": int}, ...],
        "edges": [{"label": str, "type": str, ...}, ...],
        "elapsed_ms": float
      }
    Or None if no path found within max_hops.
    """
    t0 = time.perf_counter()
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = ON;")

    row = conn.execute(
        _PATH_SQL,
        {
            "source": source_actor_id,
            "target": target_actor_id,
            "max_hops": max_hops,
            "hub_threshold": hub_degree_threshold,
        },
    ).fetchone()

    elapsed_ms = (time.perf_counter() - t0) * 1000

    if row is None:
        conn.close()
        return None

    path_ids_str  = row["path_ids"]   # e.g. "955,963,877,39"
    path_edges_str = row["path_edges"] # e.g. "rel:ACCUSED_OF:0.8|signal:xxx|signal:yyy"
    hop_count = row["hop_count"]

    actor_ids  = [int(x) for x in path_ids_str.split(",")]
    edge_labels = path_edges_str.split("|") if path_edges_str else []

    deg_table = _degree_table(conn)

    path_nodes = []
    for aid in actor_ids:
        info = _actor_info(conn, aid)
        info["degree"] = deg_table.get(aid, 0)
        path_nodes.append(info)

    edge_details = []
    for label in edge_labels:
        if label.startswith("signal:"):
            sig_id = label[7:]
            edge_details.append({"label": label, "type": "co_occurrence_signal",
                                  "signal_id": sig_id})
        elif label.startswith("rel[R]:"):
            parts = label[7:].split(":")
            edge_details.append({"label": label, "type": "relationship_reverse",
                                  "relation_type": parts[0] if parts else "?",
                                  "confidence": float(parts[1]) if len(parts) > 1 else None})
        elif label.startswith("rel:"):
            parts = label[4:].split(":")
            edge_details.append({"label": label, "type": "relationship",
                                  "relation_type": parts[0] if parts else "?",
                                  "confidence": float(parts[1]) if len(parts) > 1 else None})
        else:
            edge_details.append({"label": label, "type": "unknown"})

    conn.close()

    return {
        "hops": hop_count,
        "path": path_nodes,
        "edges": edge_details,
        "elapsed_ms": round(elapsed_ms, 2),
        "source": source_actor_id,
        "target": target_actor_id,
        "hub_threshold": hub_degree_threshold,
        "max_hops_allowed": max_hops,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SECONDARY: neighbourhood exploration
# ─────────────────────────────────────────────────────────────────────────────
_NEIGHBOURS_SQL = """
WITH RECURSIVE
  neighbourhood (actor_id, depth) AS (
    SELECT :center, 0
    UNION
    SELECT
      CASE WHEN sa1.actor_id = n.actor_id THEN sa2.actor_id
           ELSE sa1.actor_id END,
      n.depth + 1
    FROM neighbourhood n
    JOIN signal_actors sa1 ON sa1.actor_id = n.actor_id
    JOIN signal_actors sa2 ON sa2.signal_id = sa1.signal_id AND sa2.actor_id <> n.actor_id
    WHERE n.depth < :max_depth
    UNION
    SELECT
      CASE WHEN er.subject_actor_id = n.actor_id THEN er.object_actor_id
           ELSE er.subject_actor_id END,
      n.depth + 1
    FROM neighbourhood n
    JOIN entity_relationships er
      ON er.subject_actor_id = n.actor_id OR er.object_actor_id = n.actor_id
    WHERE n.depth < :max_depth
  )
SELECT n.actor_id, MIN(n.depth) AS min_depth, a.name, a.type
FROM neighbourhood n
JOIN actors a ON a.actor_id = n.actor_id
GROUP BY n.actor_id
ORDER BY min_depth, n.actor_id
"""


def get_neighbourhood(center_id: int, max_depth: int = 2) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(_NEIGHBOURS_SQL, {"center": center_id, "max_depth": max_depth}).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# SECONDARY: network centrality metrics snapshot
# ─────────────────────────────────────────────────────────────────────────────
def compute_network_metrics(top_n: int = 20) -> list[dict]:
    """
    Returns top_n actors ranked by combined degree (signal_actors + entity_relationships).
    Also flags likely hub-poisoning candidates.
    """
    conn = get_connection()
    deg = _degree_table(conn)

    actors_cur = conn.execute(
        "SELECT actor_id, name, type, gravity_score FROM actors"
    ).fetchall()

    metrics = []
    for a in actors_cur:
        aid = a["actor_id"]
        d   = deg.get(aid, 0)
        sa_d = conn.execute(
            "SELECT COUNT(*) FROM signal_actors WHERE actor_id=?", (aid,)
        ).fetchone()[0]
        er_d = conn.execute(
            "SELECT COUNT(*) FROM entity_relationships WHERE subject_actor_id=? OR object_actor_id=?",
            (aid, aid)
        ).fetchone()[0]
        metrics.append({
            "actor_id":      aid,
            "name":          a["name"],
            "type":          a["type"],
            "gravity_score": a["gravity_score"],
            "sa_degree":     sa_d,
            "er_degree":     er_d,
            "combined_degree": d,
            "hub_risk":      d > DEFAULT_HUB_THRESHOLD,
        })

    conn.close()
    metrics.sort(key=lambda x: x["combined_degree"], reverse=True)
    return metrics[:top_n]


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT FORMATTING
# ─────────────────────────────────────────────────────────────────────────────
def _format_edge(edge: dict) -> str:
    t = edge.get("type", "unknown")
    if t == "co_occurrence_signal":
        sig = edge.get("signal_id", "")[:12]
        return f"--[co_occurrence: signal {sig}...]->"
    elif t in ("relationship", "relationship_reverse"):
        rt   = edge.get("relation_type", "LINK")
        conf = edge.get("confidence")
        rev  = " (reversed)" if t == "relationship_reverse" else ""
        conf_str = f" conf={conf:.2f}" if conf is not None else ""
        return f"--[{rt}{conf_str}{rev}]->"
    return f"--[{edge.get('label','?')}]->"


def print_path_report(result: Optional[dict], hub_threshold: int, show_degrees: bool = True):
    if result is None:
        print()
        print("  [NO PATH FOUND]")
        print(f"  No route exists within {DEFAULT_MAX_HOPS} hops at hub_threshold={hub_threshold}.")
        print()
        return

    path   = result["path"]
    edges  = result["edges"]
    hops   = result["hops"]
    elapsed = result["elapsed_ms"]

    print()
    print(f"  Path found: {hops} hop(s)   ({elapsed} ms)")
    print()

    for i, node in enumerate(path):
        deg_str = f"  [deg={node['degree']}]" if show_degrees else ""
        flag = ""
        if node["degree"] > hub_threshold and node["actor_id"] not in (
            result["source"], result["target"]
        ):
            flag = "  *** HUB WARNING ***"
        print(f"  [{node['actor_id']}] {node['name']}{deg_str}{flag}")
        if i < len(edges):
            print(f"        {_format_edge(edges[i])}")

    print()


def print_metrics_report(metrics: list[dict], hub_threshold: int):
    _print_header("NETWORK CENTRALITY SNAPSHOT")
    print(f"  {'Rank':<5} {'ID':<6} {'Name':<40} {'Deg':>5} {'SA':>5} {'ER':>4}  Hub?")
    _print_separator()
    for i, m in enumerate(metrics, 1):
        hub = "YES" if m["hub_risk"] else "---"
        name = m["name"][:39]
        print(f"  {i:<5} {m['actor_id']:<6} {name:<40} {m['combined_degree']:>5} "
              f"{m['sa_degree']:>5} {m['er_degree']:>4}  {hub}")
    _print_separator()
    n_hub = sum(1 for m in metrics if m["hub_risk"])
    print(f"  Hub-risk nodes (degree>{hub_threshold}) in top-{len(metrics)}: {n_hub}")
    print()


def print_neighbourhood_report(center_id: int, nodes: list[dict]):
    _print_header(f"NEIGHBOURHOOD MAP — Actor [{center_id}]")
    prev_depth = -1
    for n in nodes:
        d = n["min_depth"]
        if d != prev_depth:
            print(f"\n  [Depth {d}]")
            prev_depth = d
        mark = ">>>" if n["actor_id"] == center_id else "   "
        print(f"  {mark} [{n['actor_id']}] {n['name']}  ({n['type'] or '?'})")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="FORGE Phase 66 — Coalition Matrix: actor graph traversal engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python scripts/network_emergence.py --source 955 --target 39
              python scripts/network_emergence.py --source 955 --target 39 --hub-threshold 50
              python scripts/network_emergence.py --source 955 --target 39 --no-hub-filter
              python scripts/network_emergence.py --metrics --top 20
              python scripts/network_emergence.py --neighbours 955 --depth 2
        """),
    )
    ap.add_argument("--source",        type=int, help="Source actor_id")
    ap.add_argument("--target",        type=int, help="Target actor_id")
    ap.add_argument("--max-hops",      type=int, default=DEFAULT_MAX_HOPS)
    ap.add_argument("--hub-threshold", type=int, default=DEFAULT_HUB_THRESHOLD,
                    help=f"Nodes with combined degree > N are bypassed (default {DEFAULT_HUB_THRESHOLD})")
    ap.add_argument("--no-hub-filter", action="store_true",
                    help="Disable hub filtering entirely")
    ap.add_argument("--metrics",       action="store_true",
                    help="Print network centrality metrics and exit")
    ap.add_argument("--top",           type=int, default=20)
    ap.add_argument("--neighbours",    type=int, metavar="ACTOR_ID",
                    help="Explore neighbourhood of a given actor")
    ap.add_argument("--depth",         type=int, default=2)
    ap.add_argument("--json",          action="store_true",
                    help="Output raw JSON (pathfinding only)")

    args = ap.parse_args()

    hub_threshold = VERY_LARGE if args.no_hub_filter else args.hub_threshold

    # ── Metrics mode ─────────────────────────────────────────────────────────
    if args.metrics:
        metrics = compute_network_metrics(top_n=args.top)
        print_metrics_report(metrics, hub_threshold)
        return

    # ── Neighbourhood mode ───────────────────────────────────────────────────
    if args.neighbours:
        nodes = get_neighbourhood(args.neighbours, max_depth=args.depth)
        print_neighbourhood_report(args.neighbours, nodes)
        return

    # ── Pathfinding mode ─────────────────────────────────────────────────────
    if args.source is None or args.target is None:
        ap.error("--source and --target are required for pathfinding.")

    _print_header(
        f"COALITION MATRIX — PATHFINDING"
    )
    conn = get_connection()
    src_name = _actor_name(conn, args.source)
    tgt_name = _actor_name(conn, args.target)
    src_deg  = _combined_degree(conn, args.source)
    tgt_deg  = _combined_degree(conn, args.target)
    conn.close()

    hub_label = f"{hub_threshold}" if hub_threshold < VERY_LARGE else "DISABLED"
    print(f"  Source : [{args.source}] {src_name}  (degree={src_deg})")
    print(f"  Target : [{args.target}] {tgt_name}  (degree={tgt_deg})")
    print(f"  Max hops        : {args.max_hops}")
    print(f"  Hub threshold   : {hub_label}")
    _print_separator()

    result = find_shortest_path(
        source_actor_id     = args.source,
        target_actor_id     = args.target,
        max_hops            = args.max_hops,
        hub_degree_threshold = hub_threshold,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    print_path_report(result, hub_threshold)

    if result:
        _print_separator()
        print("  HOP CHAIN NARRATIVE")
        _print_separator()
        path  = result["path"]
        edges = result["edges"]
        for i in range(len(edges)):
            src_node = path[i]
            dst_node = path[i + 1]
            e = edges[i]
            etype = e.get("type", "")
            if etype == "co_occurrence_signal":
                sig = e.get("signal_id", "")
                sig_title_cur = get_connection().execute(
                    "SELECT title FROM signals WHERE signal_id=?", (sig,)
                )
                sig_row = sig_title_cur.fetchone()
                sig_label = sig_row["title"][:60] if sig_row and sig_row["title"] else sig[:20]
                desc = (f"Both actors appear in signal: \"{sig_label}\"")
            else:
                rt   = e.get("relation_type", "LINK")
                conf = e.get("confidence")
                conf_str = f" (confidence={conf:.2f})" if conf is not None else ""
                rev  = " [reversed]" if etype == "relationship_reverse" else ""
                desc = f"Direct relationship: {rt}{conf_str}{rev}"

            hop_num = i + 1
            print(f"\n  Hop {hop_num}: [{src_node['actor_id']}] {src_node['name']}")
            print(f"         --> [{dst_node['actor_id']}] {dst_node['name']}")
            print(f"         via: {desc}")

        print()
        _print_separator()
        print(f"  RESULT: {result['hops']}-hop chain confirmed in {result['elapsed_ms']} ms")
        _print_separator()
        print()


if __name__ == "__main__":
    main()

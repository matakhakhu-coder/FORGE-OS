"""
FORGE — Graph Migration Script
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Populates graph_nodes and graph_edges from existing relational tables.

Run ONCE after apply_graph_schema_patch() creates the tables.
Safe to re-run — INSERT OR IGNORE on UNIQUE constraints.

Batched to avoid locking the DB on large datasets.

Usage:
    python migrate_graph.py
    python migrate_graph.py --db /path/to/database.db
    python migrate_graph.py --dry-run
"""

from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path
from typing import Optional

BATCH_SIZE = 500


def _resolve_db(override: Optional[str] = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _get_or_create_node(conn, node_type: str, ref_id: str,
                         label: str = None, dry_run: bool = False) -> Optional[int]:
    """Get existing node_id or create new one. Returns node_id."""
    row = conn.execute(
        "SELECT node_id FROM graph_nodes WHERE node_type=? AND ref_id=?",
        (node_type, str(ref_id))
    ).fetchone()
    if row:
        return row["node_id"]
    if dry_run:
        return None
    cur = conn.execute(
        "INSERT OR IGNORE INTO graph_nodes (node_type, ref_id, label) VALUES (?,?,?)",
        (node_type, str(ref_id), label)
    )
    if cur.lastrowid:
        return cur.lastrowid
    # Race condition fallback
    row = conn.execute(
        "SELECT node_id FROM graph_nodes WHERE node_type=? AND ref_id=?",
        (node_type, str(ref_id))
    ).fetchone()
    return row["node_id"] if row else None


def _create_edge(conn, source_id: int, target_id: int,
                 relation_type: str, weight: float = 1.0,
                 confidence: float = 1.0,
                 source_event_id=None, source_signal_id=None,
                 dry_run: bool = False) -> bool:
    if dry_run or source_id is None or target_id is None:
        return False
    try:
        conn.execute("""
            INSERT OR IGNORE INTO graph_edges
                (source_node_id, target_node_id, relation_type,
                 weight, confidence, source_event_id, source_signal_id)
            VALUES (?,?,?,?,?,?,?)
        """, (source_id, target_id, relation_type,
               weight, confidence, source_event_id, source_signal_id))
        return True
    except Exception as e:
        print(f"  [Edge Error] {e}")
        return False


def migrate_nodes(conn, dry_run: bool = False) -> dict:
    """Populate graph_nodes from all entity tables."""
    counts = {}

    # Actors
    actors = conn.execute("SELECT actor_id, name, type FROM actors").fetchall()
    n = 0
    for a in actors:
        _get_or_create_node(conn, "actor", str(a["actor_id"]),
                            label=a["name"], dry_run=dry_run)
        n += 1
    if not dry_run: conn.commit()
    counts["actors"] = n
    print(f"  Nodes: {n} actors")

    # Events
    events = conn.execute("SELECT event_id, title FROM events").fetchall()
    n = 0
    for e in events:
        _get_or_create_node(conn, "event", str(e["event_id"]),
                            label=(e["title"] or "")[:80], dry_run=dry_run)
        n += 1
    if not dry_run: conn.commit()
    counts["events"] = n
    print(f"  Nodes: {n} events")

    # Cases
    cases = conn.execute("SELECT case_id, title FROM cases").fetchall()
    n = 0
    for c in cases:
        _get_or_create_node(conn, "case", str(c["case_id"]),
                            label=(c["title"] or "")[:80], dry_run=dry_run)
        n += 1
    if not dry_run: conn.commit()
    counts["cases"] = n
    print(f"  Nodes: {n} cases")

    # Signals — batched (can be large)
    total_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    n = 0
    offset = 0
    while True:
        batch = conn.execute(
            "SELECT signal_id, title FROM signals LIMIT ? OFFSET ?",
            (BATCH_SIZE, offset)
        ).fetchall()
        if not batch:
            break
        for s in batch:
            _get_or_create_node(conn, "signal", s["signal_id"],
                                label=(s["title"] or "")[:80], dry_run=dry_run)
            n += 1
        if not dry_run: conn.commit()
        offset += BATCH_SIZE
        if offset % 5000 == 0:
            print(f"    Signals: {offset}/{total_signals}...")
    counts["signals"] = n
    print(f"  Nodes: {n} signals (of {total_signals})")

    # Artifacts — batched
    total_artifacts = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    n = 0
    offset = 0
    while True:
        batch = conn.execute(
            "SELECT artifact_id, title FROM artifacts LIMIT ? OFFSET ?",
            (BATCH_SIZE, offset)
        ).fetchall()
        if not batch:
            break
        for a in batch:
            _get_or_create_node(conn, "artifact", str(a["artifact_id"]),
                                label=(a["title"] or "")[:80], dry_run=dry_run)
            n += 1
        if not dry_run: conn.commit()
        offset += BATCH_SIZE
    counts["artifacts"] = n
    print(f"  Nodes: {n} artifacts")

    return counts


def migrate_edges(conn, dry_run: bool = False) -> dict:
    """Populate graph_edges from all relationship tables."""
    counts = {}

    # actor_events → actor involved_in event
    rows = conn.execute(
        "SELECT actor_id, event_id, role FROM actor_events"
    ).fetchall()
    n = 0
    for r in rows:
        a_node = _get_or_create_node(conn, "actor", str(r["actor_id"]), dry_run=dry_run)
        e_node = _get_or_create_node(conn, "event", str(r["event_id"]), dry_run=dry_run)
        if _create_edge(conn, a_node, e_node, "involved_in",
                        source_event_id=r["event_id"], dry_run=dry_run):
            n += 1
    if not dry_run: conn.commit()
    counts["actor_events"] = n
    print(f"  Edges: {n} actor→event (actor_events)")

    # event_actors → actor involved_in event (automated pipeline)
    rows = conn.execute(
        "SELECT actor_id, event_id, role FROM event_actors"
    ).fetchall()
    n = 0
    for r in rows:
        a_node = _get_or_create_node(conn, "actor", str(r["actor_id"]), dry_run=dry_run)
        e_node = _get_or_create_node(conn, "event", str(r["event_id"]), dry_run=dry_run)
        if _create_edge(conn, a_node, e_node, "involved_in",
                        source_event_id=r["event_id"], dry_run=dry_run):
            n += 1
    if not dry_run: conn.commit()
    counts["event_actors"] = n
    print(f"  Edges: {n} actor→event (event_actors)")

    # signal_actors → signal mentions actor (batched)
    total = conn.execute("SELECT COUNT(*) FROM signal_actors").fetchone()[0]
    n = 0
    offset = 0
    while True:
        batch = conn.execute(
            "SELECT signal_id, actor_id FROM signal_actors LIMIT ? OFFSET ?",
            (BATCH_SIZE, offset)
        ).fetchall()
        if not batch:
            break
        for r in batch:
            s_node = _get_or_create_node(conn, "signal", r["signal_id"], dry_run=dry_run)
            a_node = _get_or_create_node(conn, "actor", str(r["actor_id"]), dry_run=dry_run)
            if _create_edge(conn, s_node, a_node, "mentions",
                            source_signal_id=r["signal_id"], dry_run=dry_run):
                n += 1
        if not dry_run: conn.commit()
        offset += BATCH_SIZE
    counts["signal_actors"] = n
    print(f"  Edges: {n} signal→actor (signal_actors, of {total})")

    # entity_relationships → actor affiliated_with actor
    try:
        rows = conn.execute(
            "SELECT subject_actor_id, object_actor_id, relation_type, confidence "
            "FROM entity_relationships"
        ).fetchall()
        n = 0
        for r in rows:
            a1 = _get_or_create_node(conn, "actor", str(r["subject_actor_id"]), dry_run=dry_run)
            a2 = _get_or_create_node(conn, "actor", str(r["object_actor_id"]), dry_run=dry_run)
            rel = r["relation_type"] or "related_to"
            if _create_edge(conn, a1, a2, rel,
                            confidence=float(r["confidence"] or 1.0), dry_run=dry_run):
                n += 1
        if not dry_run: conn.commit()
        counts["entity_relationships"] = n
        print(f"  Edges: {n} actor→actor (entity_relationships)")
    except Exception as e:
        print(f"  Skipped entity_relationships: {e}")

    # case_events → case contains event
    try:
        rows = conn.execute(
            "SELECT case_id, event_id FROM case_events"
        ).fetchall()
        n = 0
        for r in rows:
            c_node = _get_or_create_node(conn, "case", str(r["case_id"]), dry_run=dry_run)
            e_node = _get_or_create_node(conn, "event", str(r["event_id"]), dry_run=dry_run)
            if _create_edge(conn, c_node, e_node, "contains", dry_run=dry_run):
                n += 1
        if not dry_run: conn.commit()
        counts["case_events"] = n
        print(f"  Edges: {n} case→event (case_events)")
    except Exception as e:
        print(f"  Skipped case_events: {e}")

    # case_actors → case involves actor
    try:
        rows = conn.execute(
            "SELECT case_id, actor_id FROM case_actors"
        ).fetchall()
        n = 0
        for r in rows:
            c_node = _get_or_create_node(conn, "case", str(r["case_id"]), dry_run=dry_run)
            a_node = _get_or_create_node(conn, "actor", str(r["actor_id"]), dry_run=dry_run)
            if _create_edge(conn, c_node, a_node, "involves", dry_run=dry_run):
                n += 1
        if not dry_run: conn.commit()
        counts["case_actors"] = n
        print(f"  Edges: {n} case→actor (case_actors)")
    except Exception as e:
        print(f"  Skipped case_actors: {e}")

    return counts


def run_migration(db_path: Path, dry_run: bool = False) -> None:
    print(f"[Graph Migration] Database: {db_path}")
    print(f"[Graph Migration] Dry run:  {dry_run}")

    conn = _open_db(db_path)

    # Verify tables exist
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "graph_nodes" not in tables or "graph_edges" not in tables:
        print("[ERROR] graph_nodes/graph_edges tables not found.")
        print("  Run: python fix_schema.py  first")
        conn.close()
        return

    print("\n── Phase 1: Migrate nodes ──────────────────────────────")
    node_counts = migrate_nodes(conn, dry_run=dry_run)

    print("\n── Phase 2: Migrate edges ──────────────────────────────")
    edge_counts = migrate_edges(conn, dry_run=dry_run)

    conn.close()

    total_nodes = sum(node_counts.values())
    total_edges = sum(edge_counts.values())
    print(f"\n── Migration complete ──────────────────────────────────")
    print(f"  Total nodes created: {total_nodes}")
    print(f"  Total edges created: {total_edges}")
    if dry_run:
        print("  DRY RUN — no writes made")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Graph Migration — populate graph_nodes and graph_edges"
    )
    parser.add_argument("--db",      type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = _resolve_db(str(args.db) if args.db else None)
    run_migration(db, dry_run=args.dry_run)
"""
actor_maintenance.py — Phase 67: Identity Reconciliation
=========================================================
Canonical actor merge and safe deletion utility for the FORGE actor graph.

OPERATIONS
  merge_actors(canonical_id, alias_id)
      Re-point all junction-table rows from alias to canonical using the
      INSERT OR IGNORE + DELETE pattern, which safely handles UNIQUE
      constraint collisions without aborting the transaction.

  delete_actor(actor_id)
      Safely remove an actor and all its junction-table references.
      With PRAGMA foreign_keys = ON, cascades handle child tables;
      this function explicitly cleans non-CASCADE tables first.

USAGE
  python scripts/actor_maintenance.py merge --canonical 43 --alias 63
  python scripts/actor_maintenance.py delete --actor 877
  python scripts/actor_maintenance.py merge --canonical 43 --alias 63 --dry-run
  python scripts/actor_maintenance.py audit --actor 63
"""

import sys
import argparse
import textwrap
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from core.db.connection import get_connection  # noqa: E402

LINE = "-" * 72


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[{_ts()}] [actor_maintenance] {msg}", flush=True)


def _actor_label(conn, actor_id: int) -> str:
    row = conn.execute(
        "SELECT name, type FROM actors WHERE actor_id = ?", (actor_id,)
    ).fetchone()
    if row:
        return f"[{actor_id}] {row['name']} ({row['type'] or '?'})"
    return f"[{actor_id}] <NOT FOUND>"


def _degree_snapshot(conn, actor_id: int) -> dict:
    """Count rows referencing actor_id across all junction tables."""
    return {
        "signal_actors":          conn.execute(
            "SELECT COUNT(*) FROM signal_actors WHERE actor_id=?", (actor_id,)
        ).fetchone()[0],
        "event_actors":           conn.execute(
            "SELECT COUNT(*) FROM event_actors WHERE actor_id=?", (actor_id,)
        ).fetchone()[0],
        "actor_events":           conn.execute(
            "SELECT COUNT(*) FROM actor_events WHERE actor_id=?", (actor_id,)
        ).fetchone()[0],
        "entity_relationships_subj": conn.execute(
            "SELECT COUNT(*) FROM entity_relationships WHERE subject_actor_id=?", (actor_id,)
        ).fetchone()[0],
        "entity_relationships_obj":  conn.execute(
            "SELECT COUNT(*) FROM entity_relationships WHERE object_actor_id=?", (actor_id,)
        ).fetchone()[0],
        "case_actors":            conn.execute(
            "SELECT COUNT(*) FROM case_actors WHERE actor_id=?", (actor_id,)
        ).fetchone()[0],
        "sentinel_alerts_actor":  conn.execute(
            "SELECT COUNT(*) FROM sentinel_alerts WHERE actor_id=?", (actor_id,)
        ).fetchone()[0],
        "sentinel_alerts_related": conn.execute(
            "SELECT COUNT(*) FROM sentinel_alerts WHERE related_actor_id=?", (actor_id,)
        ).fetchone()[0],
        "observer_promotion_log": conn.execute(
            "SELECT COUNT(*) FROM observer_promotion_log WHERE actor_id=?", (actor_id,)
        ).fetchone()[0] if _table_exists(conn, "observer_promotion_log") else 0,
    }


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _col_exists(conn, table: str, col: str) -> bool:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    return col in cols


# ─────────────────────────────────────────────────────────────────────────────
# MERGE
# ─────────────────────────────────────────────────────────────────────────────

def merge_actors(
    canonical_id: int,
    alias_id: int,
    dry_run: bool = False,
) -> dict:
    """
    Merge alias_id into canonical_id.

    For every junction table:
      1. INSERT OR IGNORE canonical_id rows (non-colliding rows land; duplicates drop).
      2. DELETE all alias_id rows (collisions and new-claims both swept).

    entity_relationships handles subject and object sides independently and
    discards any edge that would become a self-loop (subject == object == canonical).

    With PRAGMA foreign_keys = ON active in get_connection(), the final
    DELETE FROM actors CASCADE-cleans any child rows not already migrated.

    Returns a summary dict of rows moved/dropped per table.
    """
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = ON;")

    if canonical_id == alias_id:
        raise ValueError("canonical_id and alias_id must be different.")

    canonical_label = _actor_label(conn, canonical_id)
    alias_label     = _actor_label(conn, alias_id)

    _log(f"{'DRY-RUN ' if dry_run else ''}MERGE: {alias_label}  -->  {canonical_label}")
    print(LINE)
    print(f"  Canonical : {canonical_label}")
    print(f"  Alias     : {alias_label}")
    print(f"  Dry-run   : {dry_run}")
    print(LINE)

    pre_canonical = _degree_snapshot(conn, canonical_id)
    pre_alias     = _degree_snapshot(conn, alias_id)

    summary = {}

    def _merge_table(table, actor_col, other_col, extra_cols=None):
        """Generic INSERT OR IGNORE + DELETE for tables with (actor_col, other_col) UNIQUE."""
        extra_cols = extra_cols or []
        cols_str  = ", ".join([actor_col, other_col] + extra_cols)
        sel_extra = ", ".join([f"a.{c}" for c in extra_cols]) if extra_cols else ""
        sel_all   = f"a.{other_col}" + (f", {sel_extra}" if sel_extra else "")

        alias_count  = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {actor_col}=?", (alias_id,)
        ).fetchone()[0]

        if alias_count == 0:
            summary[table] = {"alias_rows": 0, "inserted": 0, "dropped": 0}
            print(f"  {table:<32} alias_rows=0  (nothing to do)")
            return

        # Count collisions (rows that already exist under canonical for same other_col)
        collisions = conn.execute(
            f"""SELECT COUNT(*) FROM {table} a
                WHERE a.{actor_col} = ?
                AND EXISTS (
                    SELECT 1 FROM {table} b
                    WHERE b.{actor_col} = ? AND b.{other_col} = a.{other_col}
                )""",
            (alias_id, canonical_id),
        ).fetchone()[0]

        inserted_target  = alias_count - collisions
        print(f"  {table:<32} alias_rows={alias_count}  "
              f"collisions={collisions}  will_insert={inserted_target}")

        if not dry_run:
            # Phase 1: claim non-colliding rows
            conn.execute(
                f"""INSERT OR IGNORE INTO {table} ({cols_str})
                    SELECT :canon, {sel_all}
                    FROM {table} a
                    WHERE a.{actor_col} = :alias""",
                {"canon": canonical_id, "alias": alias_id},
            )
            # Phase 2: delete all alias rows
            conn.execute(
                f"DELETE FROM {table} WHERE {actor_col} = ?", (alias_id,)
            )

        summary[table] = {
            "alias_rows": alias_count,
            "inserted": inserted_target,
            "dropped": collisions,
        }

    # ── signal_actors  UNIQUE(signal_id, actor_id) ──────────────────────────
    _merge_table("signal_actors", "actor_id", "signal_id", ["role", "created_at"])

    # ── event_actors   UNIQUE(event_id, actor_id) ───────────────────────────
    _merge_table("event_actors", "actor_id", "event_id", ["role", "created_at"])

    # ── actor_events   UNIQUE(actor_id, event_id) — composite PK ───────────
    _merge_table("actor_events", "actor_id", "event_id", ["role"])

    # ── case_actors    UNIQUE(case_id, actor_id) — composite PK ─────────────
    _merge_table("case_actors", "actor_id", "case_id", ["note", "pinned_at", "sequence_order", "transition_note"])

    # ── entity_relationships: handle subject and object sides separately ─────
    # UNIQUE(subject_actor_id, object_actor_id, relation_type)
    # We must handle subject_alias→canonical and object_alias→canonical independently.
    # Guard: skip rows that would become self-loops (subj == obj == canonical).

    for side, src_col, other_col in [
        ("subject", "subject_actor_id", "object_actor_id"),
        ("object",  "object_actor_id",  "subject_actor_id"),
    ]:
        alias_count = conn.execute(
            f"SELECT COUNT(*) FROM entity_relationships WHERE {src_col}=?", (alias_id,)
        ).fetchone()[0]

        # Self-loops that would result (other side is also the alias — after merge becomes canonical→canonical)
        self_loops = conn.execute(
            f"SELECT COUNT(*) FROM entity_relationships "
            f"WHERE {src_col}=? AND {other_col}=?",
            (alias_id, alias_id),
        ).fetchone()[0]

        # Collisions with existing canonical edges (same other_col + relation_type)
        collisions = conn.execute(
            f"""SELECT COUNT(*) FROM entity_relationships a
                WHERE a.{src_col} = ?
                AND a.{other_col} != ?
                AND EXISTS (
                    SELECT 1 FROM entity_relationships b
                    WHERE b.{src_col} = ?
                      AND b.{other_col} = a.{other_col}
                      AND b.relation_type = a.relation_type
                )""",
            (alias_id, alias_id, canonical_id),
        ).fetchone()[0]

        inserted_target = alias_count - self_loops - collisions
        tbl_label = f"entity_relationships[{side}]"
        print(f"  {tbl_label:<32} alias_rows={alias_count}  "
              f"self_loops={self_loops}  collisions={collisions}  "
              f"will_insert={inserted_target}")

        if not dry_run and alias_count > 0:
            # Phase 1: INSERT non-colliding, non-self-loop rows
            conn.execute(
                f"""INSERT OR IGNORE INTO entity_relationships
                        (subject_actor_id, object_actor_id, relation_type,
                         description, confidence, source_artifact_id,
                         source_event_id, extraction_method, created_at)
                    SELECT
                        CASE WHEN {src_col} = :alias THEN :canon ELSE subject_actor_id END,
                        CASE WHEN {other_col} = :alias THEN :canon ELSE object_actor_id END,
                        relation_type, description, confidence,
                        source_artifact_id, source_event_id, extraction_method, created_at
                    FROM entity_relationships
                    WHERE {src_col} = :alias
                      AND {other_col} != :alias""",   # exclude rows where both sides are alias
                {"alias": alias_id, "canon": canonical_id},
            )
            # Phase 2: DELETE all alias rows on this side
            conn.execute(
                f"DELETE FROM entity_relationships WHERE {src_col} = ?", (alias_id,)
            )

        summary[tbl_label] = {
            "alias_rows": alias_count,
            "inserted": inserted_target,
            "dropped": collisions + self_loops,
        }

    # ── sentinel_alerts ──────────────────────────────────────────────────────
    for col in ("actor_id", "related_actor_id"):
        if _col_exists(conn, "sentinel_alerts", col):
            cnt = conn.execute(
                f"SELECT COUNT(*) FROM sentinel_alerts WHERE {col}=?", (alias_id,)
            ).fetchone()[0]
            if cnt and not dry_run:
                conn.execute(
                    f"UPDATE sentinel_alerts SET {col}=? WHERE {col}=?",
                    (canonical_id, alias_id),
                )
            summary[f"sentinel_alerts.{col}"] = {"updated": cnt}
            print(f"  sentinel_alerts.{col:<20} updated={cnt}")

    # ── observer_promotion_log ───────────────────────────────────────────────
    if _table_exists(conn, "observer_promotion_log"):
        col = "actor_id"
        if _col_exists(conn, "observer_promotion_log", col):
            cnt = conn.execute(
                f"SELECT COUNT(*) FROM observer_promotion_log WHERE {col}=?", (alias_id,)
            ).fetchone()[0]
            if cnt and not dry_run:
                conn.execute(
                    f"UPDATE observer_promotion_log SET {col}=? WHERE {col}=?",
                    (canonical_id, alias_id),
                )
            summary["observer_promotion_log"] = {"updated": cnt}
            print(f"  observer_promotion_log.{col:<12} updated={cnt}")

    # ── Delete alias actor row ───────────────────────────────────────────────
    print(LINE)
    if not dry_run:
        conn.execute("DELETE FROM actors WHERE actor_id = ?", (alias_id,))
        conn.commit()
        _log(f"Alias actor {alias_label} deleted. Canonical {canonical_label} now owns all edges.")
    else:
        _log("DRY-RUN complete — no writes committed.")

    # Post-merge snapshot
    if not dry_run:
        post_canonical = _degree_snapshot(conn, canonical_id)
        post_alias     = _degree_snapshot(conn, alias_id)
        print()
        print("  POST-MERGE DEGREE SNAPSHOT")
        print(f"  {'Table':<35} {'Before':>8} {'After':>8}")
        print(LINE)
        for k in pre_canonical:
            b = pre_canonical[k] + pre_alias[k]
            a = post_canonical.get(k, 0)
            print(f"  {k:<35} {b:>8} {a:>8}")

    conn.close()
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# DELETE
# ─────────────────────────────────────────────────────────────────────────────

def delete_actor(actor_id: int, dry_run: bool = False) -> dict:
    """
    Delete an actor and all its junction-table rows.

    With PRAGMA foreign_keys = ON and ON DELETE CASCADE constraints,
    deleting from actors cascades to all child tables automatically.
    This function first reports what will be removed, then executes.
    """
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = ON;")

    label   = _actor_label(conn, actor_id)
    snap    = _degree_snapshot(conn, actor_id)
    total   = sum(snap.values())

    _log(f"{'DRY-RUN ' if dry_run else ''}DELETE: {label}")
    print(LINE)
    print(f"  Actor     : {label}")
    print(f"  Dry-run   : {dry_run}")
    print(LINE)
    print("  REFERENCES TO BE REMOVED:")
    for k, v in snap.items():
        if v:
            print(f"    {k:<35} {v:>6} rows")
    print(f"  {'TOTAL':<35} {total:>6} rows + actor row")
    print(LINE)

    if not dry_run:
        conn.execute("DELETE FROM actors WHERE actor_id = ?", (actor_id,))
        conn.commit()
        # Verify
        remaining = conn.execute(
            "SELECT COUNT(*) FROM actors WHERE actor_id=?", (actor_id,)
        ).fetchone()[0]
        _log(f"Deleted. Remaining actor rows with id={actor_id}: {remaining}")
    else:
        _log("DRY-RUN — no writes committed.")

    conn.close()
    return {"deleted_actor": actor_id, "label": label, "references_removed": snap}


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────────────────

def audit_actor(actor_id: int) -> None:
    """Print a full reference audit for an actor."""
    conn = get_connection()
    label = _actor_label(conn, actor_id)
    snap  = _degree_snapshot(conn, actor_id)

    print(LINE)
    print(f"  ACTOR AUDIT: {label}")
    print(LINE)
    for k, v in snap.items():
        print(f"  {k:<35} {v:>6} rows")
    print(LINE)

    # Sample signals
    sigs = conn.execute(
        """SELECT s.signal_id, s.title, s.gravity_score, s.timestamp
           FROM signal_actors sa JOIN signals s ON s.signal_id = sa.signal_id
           WHERE sa.actor_id = ? ORDER BY s.gravity_score DESC LIMIT 5""",
        (actor_id,),
    ).fetchall()
    if sigs:
        print("  TOP SIGNALS:")
        for s in sigs:
            print(f"    [{s['signal_id'][:8]}...] g={s['gravity_score']:.4f}  {(s['title'] or '')[:55]}")

    # ER edges
    ers = conn.execute(
        """SELECT er.relation_type, er.confidence,
                  a1.name AS subj, a2.name AS obj
           FROM entity_relationships er
           JOIN actors a1 ON a1.actor_id = er.subject_actor_id
           JOIN actors a2 ON a2.actor_id = er.object_actor_id
           WHERE er.subject_actor_id=? OR er.object_actor_id=?
           LIMIT 10""",
        (actor_id, actor_id),
    ).fetchall()
    if ers:
        print("  RELATIONSHIPS:")
        for e in ers:
            print(f"    {e['subj']} --[{e['relation_type']} conf={e['confidence']}]--> {e['obj']}")

    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="FORGE Phase 67 — Actor Maintenance: merge and delete canonical actors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python scripts/actor_maintenance.py merge --canonical 43 --alias 63
              python scripts/actor_maintenance.py merge --canonical 43 --alias 63 --dry-run
              python scripts/actor_maintenance.py delete --actor 877
              python scripts/actor_maintenance.py delete --actor 877 --dry-run
              python scripts/actor_maintenance.py audit  --actor 63
        """),
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_merge = sub.add_parser("merge", help="Merge alias actor into canonical actor")
    p_merge.add_argument("--canonical", type=int, required=True)
    p_merge.add_argument("--alias",     type=int, required=True)
    p_merge.add_argument("--dry-run",   action="store_true")

    p_del = sub.add_parser("delete", help="Delete a noise/generic actor and all its references")
    p_del.add_argument("--actor",   type=int, required=True)
    p_del.add_argument("--dry-run", action="store_true")

    p_aud = sub.add_parser("audit", help="Print a reference audit for an actor")
    p_aud.add_argument("--actor", type=int, required=True)

    args = ap.parse_args()

    if args.command == "merge":
        merge_actors(args.canonical, args.alias, dry_run=args.dry_run)
    elif args.command == "delete":
        delete_actor(args.actor, dry_run=args.dry_run)
    elif args.command == "audit":
        audit_actor(args.actor)


if __name__ == "__main__":
    main()

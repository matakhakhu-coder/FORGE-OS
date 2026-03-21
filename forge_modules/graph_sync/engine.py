"""
graph_sync — Engine  (v1.1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Maintains the FORGE Graph Core tables in real-time.
Fixed: robust actor_id parsing from conclave_meta (handles str repr and JSON).
"""

from __future__ import annotations
import json
import logging
from typing import Dict, Any, Optional

log = logging.getLogger("forge.modules.graph_sync")


def _get_or_create_node(conn, node_type: str, ref_id: str,
                         label: str = None) -> Optional[int]:
    """Idempotent node creation. Returns node_id."""
    try:
        # Ensure label is always a string or None
        if label is not None and not isinstance(label, str):
            label = str(label)

        row = conn.execute(
            "SELECT node_id FROM graph_nodes WHERE node_type=? AND ref_id=?",
            (node_type, str(ref_id))
        ).fetchone()
        if row:
            return row[0] if not hasattr(row, "__getitem__") else row["node_id"]

        cur = conn.execute(
            "INSERT OR IGNORE INTO graph_nodes (node_type, ref_id, label) VALUES (?,?,?)",
            (node_type, str(ref_id), label)
        )
        if cur.lastrowid:
            return cur.lastrowid

        row = conn.execute(
            "SELECT node_id FROM graph_nodes WHERE node_type=? AND ref_id=?",
            (node_type, str(ref_id))
        ).fetchone()
        return row[0] if row and not hasattr(row, "__getitem__") else (row["node_id"] if row else None)
    except Exception as e:
        log.debug(f"[graph_sync] node creation failed ({node_type}/{ref_id}): {e}")
        return None


def _create_edge(conn, source_id: int, target_id: int,
                 relation_type: str, weight: float = 1.0,
                 source_signal_id: str = None,
                 source_event_id: int = None) -> bool:
    """Idempotent edge creation."""
    if source_id is None or target_id is None:
        return False
    try:
        conn.execute("""
            INSERT OR IGNORE INTO graph_edges
                (source_node_id, target_node_id, relation_type,
                 weight, source_signal_id, source_event_id)
            VALUES (?,?,?,?,?,?)
        """, (source_id, target_id, relation_type,
               weight, source_signal_id, source_event_id))
        return True
    except Exception as e:
        log.debug(f"[graph_sync] edge creation failed: {e}")
        return False


def _parse_actor_ids(raw) -> list:
    """
    Safely parse actor_ids from conclave_meta.
    Handles: JSON list, Python repr list, single int, None.
    Returns list of ints.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        ids = []
        for x in raw:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
        return ids
    if isinstance(raw, (int, float)):
        return [int(raw)]
    # String — try JSON first, then eval-safe parsing
    s = str(raw).strip()
    if not s or s in ("[]", "null", "None"):
        return []
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [int(x) for x in parsed if str(x).lstrip('-').isdigit()]
    except (json.JSONDecodeError, ValueError):
        pass
    # Python repr like "[1, 2, 3]"
    try:
        import ast
        parsed = ast.literal_eval(s)
        if isinstance(parsed, list):
            return [int(x) for x in parsed if isinstance(x, (int, float))]
    except Exception:
        pass
    return []


def sync(signal: Dict[str, Any], result: Dict[str, Any],
         conn=None) -> None:
    """
    Sync a processed signal into the graph.
    """
    if conn is None:
        return

    signal_id = signal.get("signal_id")
    if not signal_id:
        return

    try:
        title = str(signal.get("title") or "")[:80]

        # ── Signal node ───────────────────────────────────────────────────
        s_node = _get_or_create_node(conn, "signal", signal_id, label=title)

        # ── Read conclave_meta for actor_ids and event_id ─────────────────
        actor_ids = []
        event_id  = None
        try:
            meta_row = conn.execute(
                "SELECT conclave_meta FROM signals WHERE signal_id=?",
                (signal_id,)
            ).fetchone()
            if meta_row:
                raw_meta = meta_row[0] if not hasattr(meta_row, "__getitem__") else meta_row["conclave_meta"]
                if raw_meta:
                    try:
                        meta = json.loads(raw_meta)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                    actor_ids = _parse_actor_ids(meta.get("actors"))
                    event_id  = meta.get("event_id")
        except Exception as e:
            log.debug(f"[graph_sync] meta read failed: {e}")

        # ── Actor nodes + signal→actor edges ─────────────────────────────
        for actor_id in actor_ids:
            try:
                actor_row = conn.execute(
                    "SELECT name FROM actors WHERE actor_id=?", (int(actor_id),)
                ).fetchone()
                alabel = None
                if actor_row:
                    alabel = actor_row[0] if not hasattr(actor_row, "__getitem__") else actor_row["name"]
                alabel = str(alabel) if alabel else str(actor_id)
                a_node = _get_or_create_node(conn, "actor", str(actor_id), label=alabel)
                _create_edge(conn, s_node, a_node, "mentions",
                             source_signal_id=signal_id)
            except Exception as e:
                log.debug(f"[graph_sync] actor edge failed actor_id={actor_id}: {e}")

        # ── Event node + actor→event + signal→event edges ─────────────────
        if event_id:
            try:
                ev_row = conn.execute(
                    "SELECT title FROM events WHERE event_id=?", (int(event_id),)
                ).fetchone()
                ev_label = None
                if ev_row:
                    ev_label = ev_row[0] if not hasattr(ev_row, "__getitem__") else ev_row["title"]
                ev_label = str(ev_label or "")[:80]
                e_node = _get_or_create_node(conn, "event", str(event_id), label=ev_label)
                _create_edge(conn, s_node, e_node, "triggered",
                             source_signal_id=signal_id, source_event_id=int(event_id))
                for actor_id in actor_ids:
                    a_node = _get_or_create_node(conn, "actor", str(actor_id))
                    _create_edge(conn, a_node, e_node, "involved_in",
                                 source_event_id=int(event_id))
            except Exception as e:
                log.debug(f"[graph_sync] event edge failed event_id={event_id}: {e}")

        conn.commit()

    except Exception as e:
        log.error(f"[graph_sync] sync failed for signal {signal_id}: {e}")
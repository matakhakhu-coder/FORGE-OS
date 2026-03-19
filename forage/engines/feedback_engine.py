"""FORAGE Feedback Engine

Enables context-aware scoring where case outcomes and actor behavior drive future gravity.
"""

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.db.connection import get_connection

DEFAULT_ACTOR_WEIGHT = 1.0
MIN_ACTOR_WEIGHT = 0.2
MAX_ACTOR_WEIGHT = 5.0


def _ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS actor_weights (
            actor_id INTEGER PRIMARY KEY,
            weight REAL NOT NULL DEFAULT 1.0,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS case_feedback (
            case_id TEXT PRIMARY KEY,
            gravity_score REAL,
            decision TEXT,
            assigned_at TEXT
        )
        """
    )
    conn.commit()


def _clamp_weight(weight: float) -> float:
    return max(MIN_ACTOR_WEIGHT, min(MAX_ACTOR_WEIGHT, weight))


def get_actor_weight(actor_id: int, conn: Optional[sqlite3.Connection] = None) -> float:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_tables(conn)
        cur = conn.cursor()
        cur.execute("SELECT weight FROM actor_weights WHERE actor_id = ?", (actor_id,))
        row = cur.fetchone()
        if not row:
            w = DEFAULT_ACTOR_WEIGHT
            cur.execute(
                "INSERT OR REPLACE INTO actor_weights (actor_id, weight, updated_at) VALUES (?, ?, ?)",
                (actor_id, w, datetime.utcnow().isoformat()),
            )
            conn.commit()
            return w
        return float(row[0])
    finally:
        if own_conn:
            conn.close()


def update_actor_weight(actor_id: int, delta: float, conn: Optional[sqlite3.Connection] = None) -> float:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_tables(conn)
        current = get_actor_weight(actor_id, conn=conn)
        updated = _clamp_weight(current + delta)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO actor_weights (actor_id, weight, updated_at) VALUES (?, ?, ?)",
            (actor_id, updated, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return updated
    finally:
        if own_conn:
            conn.close()


def actor_influence(actors: List[Dict[str, Any]], conn: Optional[sqlite3.Connection] = None) -> float:
    if not actors:
        return 1.0

    weights = []
    for actor in actors:
        try:
            actor_id = int(actor.get("actor_id"))
        except Exception:
            continue
        weights.append(get_actor_weight(actor_id, conn=conn))

    if not weights:
        return 1.0

    # Scale effect by relative actor weight (higher weight increases gravity)
    return min(2.0, max(0.5, sum(weights) / len(weights)))


def register_case_feedback(case: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> None:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO case_feedback (case_id, gravity_score, decision, assigned_at) VALUES (?, ?, ?, ?)",
            (case.get("case_id"), float(case.get("gravity_score", 0.0)), case.get("decision"), datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def apply_feedback(signal: Dict[str, Any], actors: List[Dict[str, Any]], case: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Adjust actor weights and generate feedback influence for future scoring."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_tables(conn)

        decision = str(case.get("decision", "STORE ONLY")).upper()
        delta = 0.0

        if decision == "CREATE_CASE" or decision == "CREATE CASE":
            delta = 0.25
        elif decision == "FLAG_MONITOR" or decision == "FLAG MONITOR":
            delta = 0.12
        else:
            delta = -0.05

        # Update weights for actors in this signal
        updated = []
        for actor in actors:
            if "actor_id" not in actor:
                continue
            w = update_actor_weight(actor["actor_id"], delta, conn=conn)
            updated.append({"actor_id": actor["actor_id"], "weight": w})

        register_case_feedback(case, conn=conn)

        influence = actor_influence(actors, conn=conn)

        return {
            "actor_updates": updated,
            "new_influence": influence,
            "case_id": case.get("case_id"),
            "decision": decision,
        }
    finally:
        if own_conn:
            conn.close()

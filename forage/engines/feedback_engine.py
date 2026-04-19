"""FORAGE Feedback Engine

Enables context-aware scoring where case outcomes and actor behavior drive future gravity.

Phase 63 Fix 4 — Metabolic Guard
─────────────────────────────────
register_case_feedback() and apply_feedback() now enforce a strict NULL case_id
rejection policy. A gravity score with no case context is not preservable data —
it is a computation whose denominator is unknown. Rejected writes are logged at
CRITICAL level with full calling context so the broken caller is immediately
identifiable in the pipeline log.

Two-layer guard:
  1. apply_feedback()        — semantic: skip register call if no case_id present
  2. register_case_feedback() — write chokepoint: hard abort with diagnostic log
"""

import inspect
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.db.connection import get_connection

_feedback_log = logging.getLogger("forge.feedback_engine")

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
    # ── Phase 63 Fix 4: NULL case_id rejection gate ───────────────────────────
    # case_id is the PRIMARY KEY of case_feedback. A NULL PK has no schema
    # identity — it cannot be addressed, updated, or JOINed. A gravity score
    # without a case frame is a ratio with no denominator; it has no analytical
    # value and must not be persisted. Fail loudly so the broken caller is
    # visible in the pipeline log immediately.
    case_id = case.get("case_id")
    if not case_id or not str(case_id).strip():
        caller = inspect.stack()[1]
        _feedback_log.critical(
            "[case_feedback] WRITE REJECTED — case_id is NULL/empty. "
            "Hollow data blocked from case_feedback. "
            "decision=%r  gravity=%.4f  signal_id=%r  "
            "caller=%s:%d in %s()",
            case.get("decision"),
            float(case.get("gravity_score") or 0.0),
            case.get("signal_id"),
            caller.filename,
            caller.lineno,
            caller.function,
        )
        return  # abort — do not write
    # ─────────────────────────────────────────────────────────────────────────

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO case_feedback (case_id, gravity_score, decision, assigned_at) VALUES (?, ?, ?, ?)",
            (case_id, float(case.get("gravity_score", 0.0)), case.get("decision"), datetime.utcnow().isoformat()),
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

        # ── Phase 63 Fix 4: semantic gate — only record when a real case exists ─
        # Actor weight updates above run for all decisions (correct — weighting
        # actors based on signal outcomes improves future gravity regardless of
        # whether a formal case is opened). Case feedback, however, is only
        # meaningful when a case_id exists (gravity > 0.8 → CREATE CASE path).
        # FLAG MONITOR and STORE ONLY results have no case identity and must
        # not be written to case_feedback. register_case_feedback() enforces
        # the same rule as a write-layer safety net.
        if case.get("case_id"):
            register_case_feedback(case, conn=conn)
        else:
            _feedback_log.warning(
                "[apply_feedback] case_feedback write skipped — no case_id in "
                "case_result. decision=%r gravity=%.4f signal_id=%r",
                case.get("decision"),
                float(case.get("gravity_score") or 0.0),
                case.get("signal_id"),
            )
        # ─────────────────────────────────────────────────────────────────────

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

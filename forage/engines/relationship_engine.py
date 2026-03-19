"""
FORGE — Relationship Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Links Actors ↔ Signals and Actors ↔ Events in a non-duplicating,
idempotent relationship layer.

Tables (created by fix_schema.py):
    signal_actors  (signal_id, actor_id, role, created_at)
    event_actors   (event_id,  actor_id, role, created_at)

Both tables carry UNIQUE constraints so INSERT OR IGNORE is safe to
call repeatedly — re-processing a signal never creates duplicate rows.
"""

from datetime import datetime


def link_signal_actors(signal_id: str, actor_ids: list, db) -> int:
    """
    Link a signal to one or more actors.
    Returns number of new rows inserted (0 if all already existed).
    """
    inserted = 0
    for actor_id in actor_ids:
        try:
            cur = db.execute(
                """
                INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role, created_at)
                VALUES (?, ?, 'mentioned', ?)
                """,
                (signal_id, actor_id, datetime.utcnow()),
            )
            inserted += cur.rowcount
        except Exception as e:
            print(f"[Signal-Actor Link Error] signal={signal_id} actor={actor_id}: {e}")
    return inserted


def link_event_actors(event_id: int, actor_ids: list, db) -> int:
    """
    Link an event to one or more actors.
    Returns number of new rows inserted (0 if all already existed).
    """
    inserted = 0
    for actor_id in actor_ids:
        try:
            cur = db.execute(
                """
                INSERT OR IGNORE INTO event_actors (event_id, actor_id, role, created_at)
                VALUES (?, ?, 'involved', ?)
                """,
                (event_id, actor_id, datetime.utcnow()),
            )
            inserted += cur.rowcount
        except Exception as e:
            print(f"[Event-Actor Link Error] event={event_id} actor={actor_id}: {e}")
    return inserted


# --- MEGA RUNNER ADAPTER ---
def run_all():
    print(f"[{__name__}] Relationship engine has no batch runner — links are created inline during ingestion.")
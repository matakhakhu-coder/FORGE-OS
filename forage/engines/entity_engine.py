from datetime import datetime


def get_or_create_actor(name, db):
    """
    Idempotent actor creation.
    """
    row = db.execute(
        "SELECT id FROM actors WHERE name = ?",
        (name,)
    ).fetchone()

    if row:
        return row[0]

    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO actors (name, created_at, confidence_score, automated)
        VALUES (?, ?, ?, 1)
    """, (
        name,
        datetime.utcnow(),
        0.5
    ))

    db.commit()
    return cursor.lastrowid


def materialize_entities(conclusion, signal_id, db):
    """
    Creates actors only if confidence is sufficient.
    Links them to the signal via metadata.
    """
    if not getattr(conclusion, 'entities', None):
        return []

    if getattr(conclusion, 'confidence', 0.0) < 0.2:  # lowered: pipeline confidence rarely exceeds 0.4
        return []

    actor_ids = []

    for entity in conclusion.entities:
        actor_id = get_or_create_actor(entity, db)
        actor_ids.append(actor_id)

    db.execute("""
        UPDATE signals
        SET conclave_meta = json_patch(
            COALESCE(conclave_meta, '{}'),
            json_object('actors', json(?))
        )
        WHERE signal_id = ?
    """, (
        str(actor_ids),
        signal_id
    ))

    db.commit()
    return actor_ids
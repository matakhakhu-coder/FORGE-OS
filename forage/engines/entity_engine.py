from datetime import datetime


# Maps FMS entity types to actors table CHECK constraint values
_TYPE_MAP = {
    "person":         "person",
    "institution":    "institution",
    "government":     "institution",
    "political_party":"movement",
    "location":       "institution",
    "unknown":        "institution",
}


def get_or_create_actor(name, db, actor_type: str = "institution"):
    """
    Idempotent actor creation.
    actor_type: FMS semantic type — mapped to actors table CHECK values.
    """
    row = db.execute(
        "SELECT actor_id FROM actors WHERE name = ?",
        (name,)
    ).fetchone()

    if row:
        return row["actor_id"]

    mapped_type = _TYPE_MAP.get(actor_type, "institution")

    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO actors (name, type, created_at, confidence_score, automated)
        VALUES (?, ?, ?, ?, 1)
    """, (
        name,
        mapped_type,
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

    if getattr(conclusion, 'confidence', 0.0) < 0.4:
        return []

    actor_ids = []

    # Pull entity type map from provenance if enrichment module provided it
    entity_types = {}
    if hasattr(conclusion, "provenance") and isinstance(conclusion.provenance, dict):
        entity_types = conclusion.provenance.get("entity_types", {})

    for entity in conclusion.entities:
        actor_type = entity_types.get(entity, "institution")
        actor_id   = get_or_create_actor(entity, db, actor_type=actor_type)
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
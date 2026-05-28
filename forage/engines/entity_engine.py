from __future__ import annotations
import logging as _logging
from datetime import datetime, timezone

_log = _logging.getLogger(__name__)

# Maps FMS entity types to actors table CHECK constraint values.
# "location" stays as "institution" until a dedicated schema migration adds
# a 'location' type to the actors CHECK constraint — safe fallback.
_TYPE_MAP = {
    "person":          "person",
    "institution":     "institution",
    "government":      "institution",
    "political_party": "movement",
    "location":        "institution",
    "organization":    "institution",
    "unknown":         "institution",
    # spaCy NER tags
    "PERSON":          "person",
    "PER":             "person",
    "ORG":             "institution",
    "NORP":            "institution",
    "FAC":             "institution",
    "GPE":             "institution",
    "LOC":             "institution",
}

# Names that must never become actor records — category labels, not entities.
# Primary defense is _GENERIC_ACTOR_TERMS in signal_interpreter; this is the
# secondary guard at the DB write layer.
_BLOCKED_ACTOR_NAMES = frozenset({
    "location", "government", "minister", "department",
    "institution", "organization", "unknown",
    "company", "firm", "corporation",
})


def get_or_create_actor(name, db, actor_type: str = "institution"):
    """
    Idempotent actor creation.
    actor_type: FMS semantic type — mapped to actors table CHECK values.
    Returns None for blocked generic labels so callers can skip them cleanly.
    """
    if not name or not name.strip():
        return None
    if name.strip().lower() in _BLOCKED_ACTOR_NAMES:
        _log.debug("[entity_engine] Rejected generic label as actor name: %r", name)
        return None

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
        name.strip(),
        mapped_type,
        datetime.now(timezone.utc),
        0.5
    ))
    db.execute(
        "UPDATE actors SET source_type='live' WHERE actor_id=?",
        (cursor.lastrowid,)
    )

    db.commit()
    return cursor.lastrowid


def materialize_entities(conclusion, signal_id, db):
    """
    Creates actors from Conclave conclusion entities if confidence is sufficient.
    Gate calibrated to 0.25: covers actor weights >= 0.25 from feedback_engine
    (DEFAULT_ACTOR_WEIGHT=1.0, MIN_ACTOR_WEIGHT=0.2) while still blocking
    noise from cold-start signals with no actor history (confidence=0.1).
    """
    if not getattr(conclusion, 'entities', None):
        return []

    conf = getattr(conclusion, 'confidence', 0.0)
    if conf < 0.25:
        _log.debug(
            "[entity_engine] signal=%s confidence=%.3f below gate 0.25 — skipping materialization",
            signal_id, conf,
        )
        return []

    actor_ids = []

    # Pull entity type map from provenance if enrichment module provided it
    entity_types = {}
    if hasattr(conclusion, "provenance") and isinstance(conclusion.provenance, dict):
        entity_types = conclusion.provenance.get("entity_types", {})

    for entity in conclusion.entities:
        actor_type = entity_types.get(entity, "institution")
        actor_id   = get_or_create_actor(entity, db, actor_type=actor_type)
        if actor_id is not None:
            actor_ids.append(actor_id)

    if not actor_ids:
        return []

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


def link_actors(
    source_actor_id: int,
    target_actor_id: int,
    relation_type: str,
    weight: float,
    db,
) -> bool:
    """
    Insert a directed graph_edges row between two actors via their graph_nodes entries.
    Returns True if the edge was written or already existed; False if either actor
    has no graph_node record yet (silently skipped — not an error).
    """
    src = db.execute(
        "SELECT node_id FROM graph_nodes WHERE node_type='actor' AND ref_id=?",
        (source_actor_id,),
    ).fetchone()
    tgt = db.execute(
        "SELECT node_id FROM graph_nodes WHERE node_type='actor' AND ref_id=?",
        (target_actor_id,),
    ).fetchone()
    if not src or not tgt:
        return False
    try:
        db.execute(
            """
            INSERT OR IGNORE INTO graph_edges
                (source_node_id, target_node_id, relation_type, weight, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (src["node_id"], tgt["node_id"], relation_type, weight,
             datetime.now(timezone.utc).isoformat()),
        )
    except Exception as exc:
        _log.debug("[entity_engine] link_actors failed: %s", exc)
        return False
    return True


def stitch_entity_cooccurrence(signal_id: str, db) -> int:
    """
    For a given signal, find co-mentioned PERSON and ORG entities in
    signal_entities, resolve them to existing actors, and insert member_of
    edges into graph_edges.  Only creates edges — never creates actors.
    Returns the number of edges written.
    """
    rows = db.execute(
        "SELECT text, label FROM signal_entities WHERE signal_id=?",
        (signal_id,),
    ).fetchall()

    persons = [r["text"] for r in rows if r["label"] in ("PERSON", "PER")]
    orgs    = [r["text"] for r in rows if r["label"] in ("ORG", "NORP", "FAC")]

    if not persons or not orgs:
        return 0

    linked = 0
    for pname in persons:
        prow = db.execute(
            "SELECT actor_id FROM actors WHERE name=?", (pname,)
        ).fetchone()
        if not prow:
            continue
        for oname in orgs:
            orow = db.execute(
                "SELECT actor_id FROM actors WHERE name=?", (oname,)
            ).fetchone()
            if not orow:
                continue
            if link_actors(prow["actor_id"], orow["actor_id"], "member_of", 0.5, db):
                linked += 1

    if linked:
        db.commit()
    return linked
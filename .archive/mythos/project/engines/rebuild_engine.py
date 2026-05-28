#!/usr/bin/env python3
from __future__ import annotations
"""
Mythos Anthology — Recursive Rebuild Engine  (mythos/engines/rebuild_engine.py)
================================================================================
Implements the atomic node rebuild loop:

  source → character → narrative → dialogue → media → feeds_back_to → source

Each public function:
  1. Performs its operation on the named node.
  2. Writes one or more edges to mythos_edges recording what spawned what.
  3. Enqueues the *next* downstream operation in mythos_rebuild_queue.

Callers only need to enqueue the first step; the engine drives itself from there.

Rebuild chain (default):
  extract_character   — mine a source node for character data
  spawn_narrative     — produce a narrative node from character + source
  spawn_dialogue      — build / refresh the AI persona config from narratives
  spawn_media         — create a media output record from a narrative
  refresh_canon       — re-score character confidence after new content lands
  score_confidence    — recompute character.confidence_score [0.0–1.0]

All functions are no-ops if the prerequisite data is absent — they log and exit
cleanly so the queue runner can mark the job 'skipped' rather than 'failed'.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("forge.mythos.rebuild")

_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "database.db")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_edge(
    conn: sqlite3.Connection,
    *,
    src_type: str,
    src_id: str,
    tgt_type: str,
    tgt_id: str,
    edge_type: str,
    weight: float = 1.0,
    meta: dict | None = None,
) -> str:
    conn.execute(
        """INSERT INTO mythos_edges
               (source_node_type, source_node_id,
                target_node_type, target_node_id,
                edge_type, weight, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (src_type, src_id, tgt_type, tgt_id, edge_type, weight,
         json.dumps(meta or {})),
    )
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    # edge_id is hex — fetch it back
    edge_row = conn.execute(
        """SELECT edge_id FROM mythos_edges
           WHERE source_node_id=? AND target_node_id=? AND edge_type=?
           ORDER BY created_at DESC LIMIT 1""",
        (src_id, tgt_id, edge_type),
    ).fetchone()
    return edge_row["edge_id"] if edge_row else ""


def _enqueue(
    conn: sqlite3.Connection,
    *,
    node_type: str,
    node_id: str,
    operation: str,
    trigger_edge_id: str | None = None,
    priority: int = 5,
) -> None:
    conn.execute(
        """INSERT INTO mythos_rebuild_queue
               (node_type, node_id, operation, trigger_edge_id, priority)
           VALUES (?, ?, ?, ?, ?)""",
        (node_type, node_id, operation, trigger_edge_id, priority),
    )


def _mark_queue(conn: sqlite3.Connection, queue_id: str, status: str,
                result: dict | None = None, error: str | None = None) -> None:
    conn.execute(
        """UPDATE mythos_rebuild_queue
           SET status=?, processed_at=?, result_json=?, error_text=?
           WHERE queue_id=?""",
        (status, _now(), json.dumps(result or {}), error, queue_id),
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_character(source_id: str, queue_id: str | None = None) -> dict:
    """
    Mine a source node and create / update a character stub.

    This is a scaffold — the actual NLP extraction lives in
    mythos/processors/character_extractor.py and will be wired in later.
    For now the function creates a minimal stub row so the rebuild chain
    has something to attach to.

    Returns: {'character_id': str, 'created': bool}
    """
    try:
        conn = _conn()
        try:
            src = conn.execute(
                "SELECT * FROM mythos_sources WHERE source_id=?", (source_id,)
            ).fetchone()
            if not src:
                log.warning("[mythos] extract_character: source %s not found", source_id)
                if queue_id:
                    _mark_queue(conn, queue_id, "skipped",
                                error=f"source {source_id} not found")
                    conn.commit()
                return {}

            # Resolve culture → use as canonical_name placeholder until extractor runs
            culture = src["culture"] or "Unknown"
            placeholder_name = f"[{culture}] {src['title'][:40]}"

            existing = conn.execute(
                "SELECT character_id FROM mythos_characters WHERE canonical_name=?",
                (placeholder_name,),
            ).fetchone()

            created = False
            if existing:
                char_id = existing["character_id"]
            else:
                conn.execute(
                    """INSERT INTO mythos_characters
                           (canonical_name, culture, status, confidence_score)
                       VALUES (?, ?, 'stub', 0.05)""",
                    (placeholder_name, culture),
                )
                char_id = conn.execute(
                    "SELECT character_id FROM mythos_characters WHERE canonical_name=?",
                    (placeholder_name,),
                ).fetchone()["character_id"]
                created = True

            edge_id = _write_edge(
                conn,
                src_type="character", src_id=char_id,
                tgt_type="source",    tgt_id=source_id,
                edge_type="spawned_from",
                meta={"operation": "extract_character"},
            )

            # Enqueue narrative synthesis as the next step
            _enqueue(conn, node_type="character", node_id=char_id,
                     operation="spawn_narrative",
                     trigger_edge_id=edge_id, priority=5)

            if queue_id:
                _mark_queue(conn, queue_id, "complete",
                            result={"character_id": char_id, "created": created})
            conn.commit()
            log.info("[mythos] extract_character: source=%s → char=%s (new=%s)",
                     source_id[:8], char_id[:8], created)
            return {"character_id": char_id, "created": created}

        finally:
            conn.close()

    except Exception as exc:
        log.exception("[mythos] extract_character failed: %s", exc)
        return {}


def spawn_narrative(character_id: str, queue_id: str | None = None) -> dict:
    """
    Create a narrative stub for a character, sourced from the most recent
    linked source node.

    Returns: {'narrative_id': str}
    """
    try:
        conn = _conn()
        try:
            char = conn.execute(
                "SELECT * FROM mythos_characters WHERE character_id=?",
                (character_id,),
            ).fetchone()
            if not char:
                log.warning("[mythos] spawn_narrative: char %s not found", character_id)
                if queue_id:
                    _mark_queue(conn, queue_id, "skipped",
                                error=f"character {character_id} not found")
                    conn.commit()
                return {}

            # Find a source via edges
            edge = conn.execute(
                """SELECT target_node_id FROM mythos_edges
                   WHERE source_node_id=? AND source_node_type='character'
                     AND target_node_type='source' AND edge_type='spawned_from'
                   ORDER BY created_at DESC LIMIT 1""",
                (character_id,),
            ).fetchone()
            source_id = edge["target_node_id"] if edge else None

            title = f"[stub] Origin of {char['canonical_name']}"
            body = (
                f"[Narrative scaffold for {char['canonical_name']} "
                f"({char['culture']}). "
                "Body will be generated by character_extractor.py when source text is processed.]"
            )

            conn.execute(
                """INSERT INTO mythos_narratives
                       (character_id, source_id, narrative_type,
                        title, body_text, status)
                   VALUES (?, ?, 'origin', ?, ?, 'draft')""",
                (character_id, source_id, title, body),
            )
            narr_id = conn.execute(
                "SELECT narrative_id FROM mythos_narratives WHERE title=? AND character_id=?",
                (title, character_id),
            ).fetchone()["narrative_id"]

            edge_id = _write_edge(
                conn,
                src_type="narrative", src_id=narr_id,
                tgt_type="character", tgt_id=character_id,
                edge_type="spawned_from",
                meta={"operation": "spawn_narrative"},
            )

            # Enqueue dialogue persona build
            _enqueue(conn, node_type="character", node_id=character_id,
                     operation="spawn_dialogue",
                     trigger_edge_id=edge_id, priority=6)

            if queue_id:
                _mark_queue(conn, queue_id, "complete",
                            result={"narrative_id": narr_id})
            conn.commit()
            log.info("[mythos] spawn_narrative: char=%s → narr=%s",
                     character_id[:8], narr_id[:8])
            return {"narrative_id": narr_id}

        finally:
            conn.close()

    except Exception as exc:
        log.exception("[mythos] spawn_narrative failed: %s", exc)
        return {}


def spawn_dialogue(character_id: str, queue_id: str | None = None) -> dict:
    """
    Build or refresh an AI persona config for a character.
    Generates a base system prompt from the character's canonical data
    and the most recent published narrative.

    Returns: {'dialogue_id': str, 'created': bool}
    """
    try:
        conn = _conn()
        try:
            char = conn.execute(
                "SELECT * FROM mythos_characters WHERE character_id=?",
                (character_id,),
            ).fetchone()
            if not char:
                if queue_id:
                    _mark_queue(conn, queue_id, "skipped",
                                error=f"character {character_id} not found")
                    conn.commit()
                return {}

            # Pull best narrative for prompt context
            narr = conn.execute(
                """SELECT title, body_text FROM mythos_narratives
                   WHERE character_id=? ORDER BY
                     CASE status WHEN 'published' THEN 0
                                 WHEN 'reviewed'  THEN 1
                                 ELSE 2 END,
                     created_at DESC LIMIT 1""",
                (character_id,),
            ).fetchone()

            traits = json.loads(char["traits_json"] or "[]")
            powers = json.loads(char["powers_json"] or "[]")
            trait_str = ", ".join(traits) if traits else "unknown"
            power_str = ", ".join(powers) if powers else "unknown"

            persona = (
                f"You are {char['canonical_name']}, a figure from {char['culture']} mythology "
                f"of the {char['era'] or 'ancient'} era. "
                f"Your archetype is: {char['archetype'] or 'unknown'}. "
                f"Your traits include: {trait_str}. "
                f"Your domains and powers include: {power_str}. "
            )
            if narr:
                persona += (
                    f"\n\nThe story told of you begins:\n{narr['body_text'][:500]}"
                )
            persona += (
                "\n\nSpeak in first person. Embody this character fully. "
                "Do not break character or acknowledge you are an AI."
            )

            existing = conn.execute(
                "SELECT dialogue_id FROM mythos_dialogues WHERE character_id=? AND status!='inactive' LIMIT 1",
                (character_id,),
            ).fetchone()

            created = False
            if existing:
                dial_id = existing["dialogue_id"]
                conn.execute(
                    "UPDATE mythos_dialogues SET persona_prompt=?, updated_at=? WHERE dialogue_id=?",
                    (persona, _now(), dial_id),
                )
            else:
                conn.execute(
                    """INSERT INTO mythos_dialogues
                           (character_id, persona_prompt, status)
                       VALUES (?, ?, 'inactive')""",
                    (character_id, persona),
                )
                dial_id = conn.execute(
                    "SELECT dialogue_id FROM mythos_dialogues WHERE character_id=? ORDER BY created_at DESC LIMIT 1",
                    (character_id,),
                ).fetchone()["dialogue_id"]
                created = True

            edge_id = _write_edge(
                conn,
                src_type="dialogue",  src_id=dial_id,
                tgt_type="character", tgt_id=character_id,
                edge_type="spawned_from",
                meta={"operation": "spawn_dialogue"},
            )

            # Enqueue media output planning
            _enqueue(conn, node_type="character", node_id=character_id,
                     operation="spawn_media",
                     trigger_edge_id=edge_id, priority=7)

            # Also enqueue confidence scoring
            _enqueue(conn, node_type="character", node_id=character_id,
                     operation="score_confidence", priority=4)

            if queue_id:
                _mark_queue(conn, queue_id, "complete",
                            result={"dialogue_id": dial_id, "created": created})
            conn.commit()
            log.info("[mythos] spawn_dialogue: char=%s → dial=%s (new=%s)",
                     character_id[:8], dial_id[:8], created)
            return {"dialogue_id": dial_id, "created": created}

        finally:
            conn.close()

    except Exception as exc:
        log.exception("[mythos] spawn_dialogue failed: %s", exc)
        return {}


def spawn_media(character_id: str, queue_id: str | None = None) -> dict:
    """
    Create planned media output records for a character.
    Default outputs: one podcast episode stub + one article stub.
    Populates the media table so the production pipeline has targets to fill.

    Returns: {'media_ids': list[str]}
    """
    try:
        conn = _conn()
        try:
            char = conn.execute(
                "SELECT canonical_name FROM mythos_characters WHERE character_id=?",
                (character_id,),
            ).fetchone()
            if not char:
                if queue_id:
                    _mark_queue(conn, queue_id, "skipped")
                    conn.commit()
                return {}

            narr = conn.execute(
                "SELECT narrative_id, title FROM mythos_narratives WHERE character_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (character_id,),
            ).fetchone()
            narr_id = narr["narrative_id"] if narr else None

            outputs = [
                ("podcast_episode", f"Mythos Podcast: {char['canonical_name']}", "spotify"),
                ("article",         f"Who Is {char['canonical_name']}?",         "site"),
            ]
            media_ids = []
            for mtype, title, platform in outputs:
                existing = conn.execute(
                    "SELECT media_id FROM mythos_media WHERE character_id=? AND media_type=? LIMIT 1",
                    (character_id, mtype),
                ).fetchone()
                if existing:
                    media_ids.append(existing["media_id"])
                    continue
                conn.execute(
                    """INSERT INTO mythos_media
                           (character_id, narrative_id, media_type, title, platform, status)
                       VALUES (?, ?, ?, ?, ?, 'planned')""",
                    (character_id, narr_id, mtype, title, platform),
                )
                mid = conn.execute(
                    "SELECT media_id FROM mythos_media WHERE character_id=? AND media_type=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (character_id, mtype),
                ).fetchone()["media_id"]
                media_ids.append(mid)
                _write_edge(
                    conn,
                    src_type="media",     src_id=mid,
                    tgt_type="character", tgt_id=character_id,
                    edge_type="spawned_from",
                    meta={"operation": "spawn_media", "platform": platform},
                )

            if queue_id:
                _mark_queue(conn, queue_id, "complete",
                            result={"media_ids": media_ids})
            conn.commit()
            log.info("[mythos] spawn_media: char=%s → %d media records",
                     character_id[:8], len(media_ids))
            return {"media_ids": media_ids}

        finally:
            conn.close()

    except Exception as exc:
        log.exception("[mythos] spawn_media failed: %s", exc)
        return {}


def score_confidence(character_id: str, queue_id: str | None = None) -> dict:
    """
    Recompute character.confidence_score based on node completeness.

    Scoring weights (must sum to 1.0):
      has_narrative      0.30
      has_dialogue       0.20
      has_media          0.15
      has_traits         0.15
      has_powers         0.10
      has_symbols        0.05
      has_variants       0.05

    Returns: {'confidence_score': float}
    """
    try:
        conn = _conn()
        try:
            char = conn.execute(
                "SELECT * FROM mythos_characters WHERE character_id=?",
                (character_id,),
            ).fetchone()
            if not char:
                if queue_id:
                    _mark_queue(conn, queue_id, "skipped")
                    conn.commit()
                return {}

            def _has_narr() -> bool:
                return bool(conn.execute(
                    "SELECT 1 FROM mythos_narratives WHERE character_id=? LIMIT 1",
                    (character_id,),
                ).fetchone())

            def _has_dial() -> bool:
                return bool(conn.execute(
                    "SELECT 1 FROM mythos_dialogues WHERE character_id=? LIMIT 1",
                    (character_id,),
                ).fetchone())

            def _has_media() -> bool:
                return bool(conn.execute(
                    "SELECT 1 FROM mythos_media WHERE character_id=? LIMIT 1",
                    (character_id,),
                ).fetchone())

            def _list_nonempty(col: str) -> bool:
                val = char[col] or "[]"
                try:
                    return len(json.loads(val)) > 0
                except Exception:
                    return False

            score = (
                0.30 * _has_narr() +
                0.20 * _has_dial() +
                0.15 * _has_media() +
                0.15 * _list_nonempty("traits_json") +
                0.10 * _list_nonempty("powers_json") +
                0.05 * _list_nonempty("symbols_json") +
                0.05 * _list_nonempty("variants_json")
            )

            conn.execute(
                "UPDATE mythos_characters SET confidence_score=?, updated_at=? WHERE character_id=?",
                (round(score, 4), _now(), character_id),
            )

            if queue_id:
                _mark_queue(conn, queue_id, "complete",
                            result={"confidence_score": score})
            conn.commit()
            log.info("[mythos] score_confidence: char=%s → %.3f",
                     character_id[:8], score)
            return {"confidence_score": score}

        finally:
            conn.close()

    except Exception as exc:
        log.exception("[mythos] score_confidence failed: %s", exc)
        return {}


def refresh_canon(character_id: str, queue_id: str | None = None) -> dict:
    """
    Full refresh cycle for a character: rescore confidence, write a
    feeds_back_to edge from the most recent media back to character,
    then enqueue extract_character on any unprocessed sources.

    This closes the rebuild loop: media → feeds_back_to → character → source.

    Returns: {'refreshed': bool}
    """
    try:
        conn = _conn()
        try:
            # Latest published media → feeds_back_to → character
            media = conn.execute(
                """SELECT media_id FROM mythos_media
                   WHERE character_id=? AND status='published'
                   ORDER BY published_at DESC LIMIT 1""",
                (character_id,),
            ).fetchone()
            if media:
                _write_edge(
                    conn,
                    src_type="media",     src_id=media["media_id"],
                    tgt_type="character", tgt_id=character_id,
                    edge_type="feeds_back_to",
                    meta={"operation": "refresh_canon"},
                )

            # Enqueue confidence rescore
            _enqueue(conn, node_type="character", node_id=character_id,
                     operation="score_confidence", priority=3)

            if queue_id:
                _mark_queue(conn, queue_id, "complete", result={"refreshed": True})
            conn.commit()
            log.info("[mythos] refresh_canon: char=%s", character_id[:8])
            return {"refreshed": True}

        finally:
            conn.close()

    except Exception as exc:
        log.exception("[mythos] refresh_canon failed: %s", exc)
        return {}


# ── Queue runner — called by the FMS engine on each tick ─────────────────────

_OPERATION_MAP: dict[str, callable] = {
    "extract_character": lambda q: extract_character(q["node_id"], q["queue_id"]),
    "spawn_narrative":   lambda q: spawn_narrative(q["node_id"], q["queue_id"]),
    "spawn_dialogue":    lambda q: spawn_dialogue(q["node_id"], q["queue_id"]),
    "spawn_media":       lambda q: spawn_media(q["node_id"], q["queue_id"]),
    "score_confidence":  lambda q: score_confidence(q["node_id"], q["queue_id"]),
    "refresh_canon":     lambda q: refresh_canon(q["node_id"], q["queue_id"]),
}


def process_queue(batch_size: int = 10) -> int:
    """
    Drain up to `batch_size` pending queue items in priority order.
    Returns the number of items processed.
    Called by the FMS engine on a scheduled tick or manually.
    """
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                """SELECT queue_id, node_type, node_id, operation
                   FROM mythos_rebuild_queue
                   WHERE status='pending'
                   ORDER BY priority ASC, created_at ASC
                   LIMIT ?""",
                (batch_size,),
            ).fetchall()

            for row in rows:
                qid = row["queue_id"]
                conn.execute(
                    "UPDATE mythos_rebuild_queue SET status='processing' WHERE queue_id=?",
                    (qid,),
                )
                conn.commit()

                fn = _OPERATION_MAP.get(row["operation"])
                if fn:
                    fn({"node_id": row["node_id"], "queue_id": qid})
                else:
                    _mark_queue(conn, qid, "failed",
                                error=f"unknown operation: {row['operation']}")
                    conn.commit()

            return len(rows)

        finally:
            conn.close()

    except Exception as exc:
        log.exception("[mythos] process_queue failed: %s", exc)
        return 0

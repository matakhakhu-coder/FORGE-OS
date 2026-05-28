#!/usr/bin/env python3
from __future__ import annotations
"""
Mythos Anthology — FMS Engine  (forge_modules/mythos/engine.py)
===============================================================
Registered as 'mythos_rebuild_engine'. Called by run_conclave_with_modules()
for every ingested signal.

Returns None for signals with no mythology relevance — zero overhead on
the OSINT pipeline for unrelated signals.

When a signal IS relevant (keyword match against character registry), it:
  1. Records the cross-reference in mythos_edges.
  2. Enqueues a refresh_canon operation so the character node re-evaluates
     its confidence score given new external intelligence.
"""

import logging

log = logging.getLogger("forge.mythos.engine")


def run(signal: dict, context) -> None | dict:
    """
    FMS engine entry point. Receives every signal post-Conclave.
    Returns None (no result) unless we find a mythology relevance hit.
    """
    import json
    import sqlite3
    from pathlib import Path

    content = (signal.get("content") or "").lower()
    if not content:
        return None

    db_path = str(Path(__file__).resolve().parent.parent.parent / "database.db")

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            # Pull canonical names + aliases to check for hits
            chars = conn.execute(
                "SELECT character_id, canonical_name, aliases_json FROM mythos_characters"
            ).fetchall()

            hits = []
            for char in chars:
                name = char["canonical_name"].lower()
                if name in content:
                    hits.append(char["character_id"])
                    continue
                try:
                    aliases = json.loads(char["aliases_json"] or "[]")
                    if any(a.lower() in content for a in aliases):
                        hits.append(char["character_id"])
                except Exception:
                    pass

            if not hits:
                return None

            signal_id = signal.get("signal_id", "")
            for char_id in hits:
                # Write signal → character edge into mythos graph
                conn.execute(
                    """INSERT INTO mythos_edges
                           (source_node_type, source_node_id,
                            target_node_type, target_node_id,
                            edge_type, weight, metadata_json)
                       VALUES ('source', ?, 'character', ?, 'references', 0.5, ?)""",
                    (signal_id, char_id, json.dumps({"forge_signal": True})),
                )
                # Enqueue a canon refresh so confidence_score stays current
                conn.execute(
                    """INSERT INTO mythos_rebuild_queue
                           (node_type, node_id, operation, priority)
                       VALUES ('character', ?, 'refresh_canon', 8)""",
                    (char_id,),
                )

            conn.commit()
            log.debug("[mythos engine] signal=%s → %d character hits",
                      signal_id[:8] if signal_id else "?", len(hits))
            return {"mythos_hits": len(hits), "character_ids": hits}

        finally:
            conn.close()

    except Exception as exc:
        log.warning("[mythos engine] signal scan failed: %s", exc)
        return None

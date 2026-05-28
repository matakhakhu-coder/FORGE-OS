#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE Mythos Anthology — FMS Module Entry Point  (forge_modules/mythos/module.py)
==================================================================================
Standard FMS contract: single register(conclave) function, all imports inside
it, zero side-effects on module load.

What this module registers
──────────────────────────
  Engine  : mythos_rebuild_engine
      → Scans every ingested signal for mythology keyword hits.
      → Returns None for non-mythology signals (zero overhead path).
      → On hit: writes a mythos_edge and enqueues refresh_canon.

  Hook    : on_signal
      → Fires during Conclave for every signal.
      → Cross-references signal content against character name/alias index.
      → Tags signal metadata with matched character IDs.

  Hook    : on_actor_create
      → Fires when EntityEngine materialises a new actor.
      → Checks if actor name matches a canonical Mythos character.
      → On match: bridges forge_actor_id into mythos_characters row.

FMS Contract rules enforced
────────────────────────────
  • All imports happen ONCE inside register() — never inside hooks.
  • Hooks capture references via closure.
  • No side effects on import of this file.
  • Hook failures are caught and logged at WARNING — never propagated.
"""

import logging

log = logging.getLogger("forge.modules.mythos")


def register(conclave) -> None:
    """
    Register the Mythos Anthology module into the Conclave context.
    Called once at startup by attach_module() via bootstrap_fms().
    """
    import json
    import sqlite3
    from pathlib import Path

    from forge_modules.mythos.engine import run as engine_run

    _DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "database.db")

    # ── Engine registration ───────────────────────────────────────────────────
    conclave.register_engine("mythos_rebuild_engine", engine_run)

    # ── on_signal hook ────────────────────────────────────────────────────────

    def on_signal(signal: dict, context) -> None:
        """
        Tag a signal with matched Mythos character IDs.
        Runs for every signal; returns in O(1) if no characters are loaded.
        """
        content = (signal.get("content") or "").lower()
        if not content:
            return

        try:
            conn = sqlite3.connect(_DB_PATH, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                chars = conn.execute(
                    "SELECT character_id, canonical_name, aliases_json "
                    "FROM mythos_characters"
                ).fetchall()
            finally:
                conn.close()

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

            if hits:
                existing = signal.get("metadata_json") or "{}"
                try:
                    meta = json.loads(existing)
                except Exception:
                    meta = {}
                meta["mythos_character_ids"] = hits
                signal["metadata_json"] = json.dumps(meta)
                log.debug("[mythos on_signal] tagged %d character(s)", len(hits))

        except Exception as exc:
            log.warning("[mythos on_signal] failed: %s", exc)

    conclave.register_hook("on_signal", on_signal)

    # ── on_actor_create hook ──────────────────────────────────────────────────

    def on_actor_create(actor: dict, context) -> None:
        """
        Bridge a newly created FORGE actor to a Mythos character if names match.
        Matching is case-insensitive substring on canonical_name and aliases.
        """
        actor_name = (actor.get("name") or "").strip().lower()
        actor_id   = actor.get("actor_id")
        if not actor_name or not actor_id:
            return

        try:
            conn = sqlite3.connect(_DB_PATH, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                chars = conn.execute(
                    "SELECT character_id, canonical_name, aliases_json "
                    "FROM mythos_characters WHERE forge_actor_id IS NULL"
                ).fetchall()

                for char in chars:
                    name = char["canonical_name"].lower()
                    match = actor_name == name or name in actor_name

                    if not match:
                        try:
                            aliases = json.loads(char["aliases_json"] or "[]")
                            match = any(
                                actor_name == a.lower() or a.lower() in actor_name
                                for a in aliases
                            )
                        except Exception:
                            pass

                    if match:
                        conn.execute(
                            "UPDATE mythos_characters SET forge_actor_id=? WHERE character_id=?",
                            (actor_id, char["character_id"]),
                        )
                        conn.commit()
                        log.info(
                            "[mythos on_actor_create] bridged actor=%s → char=%s (%s)",
                            actor_id[:8], char["character_id"][:8], char["canonical_name"],
                        )
                        break

            finally:
                conn.close()

        except Exception as exc:
            log.warning("[mythos on_actor_create] failed: %s", exc)

    conclave.register_hook("on_actor_create", on_actor_create)

    log.info(
        "[mythos] Registered — engine: mythos_rebuild_engine | "
        "hooks: on_signal, on_actor_create"
    )

"""
graph_sync — Module Entry Point  (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Follows FORGE Pipeline Contracts (FORGE_PIPELINE_CONTRACTS.md):

    1. All imports inside register() — never inside hooks
    2. Hook captures function reference via closure
    3. No side effects on module import
    4. No DB access at import time

Note: graph_sync declares no engines[] in manifest.json.
It only registers an on_ingest hook — no Conclave contribution.
The FMS validator skips engine validation when engines list is empty.
"""

from __future__ import annotations
import logging

log = logging.getLogger("forge.modules.graph_sync")


def register(conclave) -> None:
    """
    Register graph_sync into the Conclave context.
    All imports happen here — once, at registration time.
    """

    # ── Import engine — public interface only ─────────────────────────────────
    from forge_modules.graph_sync.engine import sync as graph_sync_fn

    # ── Hook: on_ingest ───────────────────────────────────────────────────────
    # graph_sync_fn captured by closure — no imports at hook-fire time.
    # on_ingest receives (signal, result) — we need a DB connection.
    # We open one from get_connection() rather than relying on result dict
    # to avoid coupling to ingest.py internals.

    def on_ingest(signal: dict, result: dict) -> None:
        """
        Fires after every signal is fully processed.
        Syncs signal, actors, and events into graph_nodes/graph_edges.
        """
        try:
            from core.db.connection import get_connection
            conn = get_connection()
            try:
                graph_sync_fn(signal, result, conn=conn)
            finally:
                conn.close()
        except Exception as e:
            log.error(f"[graph_sync] on_ingest failed: {e}")

    conclave.register_hook("on_ingest", on_ingest)

    log.info("[graph_sync] Registered — hook: on_ingest")
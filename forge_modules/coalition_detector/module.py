"""
coalition_detector — Module Entry Point  (v1.1)
════════════════════════════════════════════════
Follows FORGE Pipeline Contracts:
  1. All imports inside register() — never at module level
  2. Only public run() imported from engine.py
  3. No side effects on module import

v1.1 fix: engine wrapper returns None immediately on per-signal Conclave
calls. coalition_detector is a graph-level engine — it must only run when
called directly (Control Room / API), never on every ingested signal.
"""

from __future__ import annotations
import logging

log = logging.getLogger("forge.modules.coalition_detector")


def register(conclave) -> None:
    """
    Register coalition_detector into the Conclave context.
    All imports happen here — once, at registration time.
    """

    from forge_modules.coalition_detector.engine import run as _graph_run

    # ── Engine wrapper — no-op on per-signal calls ────────────────────────────
    # Conclave calls registered engines with a signal dict on every ingest.
    # coalition_detector analyses the full actor graph, not individual signals.
    # Returning None tells Conclave this engine has nothing to contribute
    # for this signal — no cost, no side effects.
    def coalition_run(signal: dict = None, **kwargs):
        # Per-signal call from Conclave ingest — skip entirely
        if signal is not None:
            return None
        # Direct call (Control Room / API) — run full graph scan
        return _graph_run(**kwargs)

    # ── Engine registration ───────────────────────────────────────────────────
    conclave.register_engine("coalition_detector_engine", coalition_run)

    # ── Route registration ────────────────────────────────────────────────────
    def coalitions_route():
        from flask import jsonify
        from forge_modules.coalition_detector.engine import query_coalitions
        try:
            data = query_coalitions()
            return jsonify({
                "coalitions":   data,
                "total":        len(data),
                "total_actors": sum(len(c["members"]) for c in data),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    coalitions_route.__name__ = "api_graph_coalitions"
    conclave.register_route(
        "/api/graph/coalitions",
        coalitions_route,
        methods=["GET"],
    )

    log.info(
        "[coalition_detector] Registered — "
        "engine: coalition_detector_engine (graph-level, no-op on per-signal calls) | "
        "route: GET /api/graph/coalitions"
    )
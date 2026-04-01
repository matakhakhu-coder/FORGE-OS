"""
emergence_engine — Module Entry Point
══════════════════════════════════════
Follows FORGE Pipeline Contracts:
  1. All imports inside register() — never at module level
  2. Only public run() imported from engine.py
  3. No side effects on module import

emergence_engine is a graph-level, time-window engine.  It must only run
when called directly (Control Room / API) — never on every ingested signal.
Per-signal Conclave calls return None immediately at zero cost.
"""

from __future__ import annotations
import logging

log = logging.getLogger("forge.modules.emergence_engine")


def register(conclave) -> None:
    """
    Register emergence_engine into the Conclave context.
    All imports happen here — once, at registration time.
    """

    from forge_modules.emergence_engine.engine import run as _emergence_run

    # ── Engine wrapper — no-op on per-signal calls ────────────────────────────
    def emergence_run(signal: dict = None, **kwargs):
        # Per-signal call from Conclave ingest — skip entirely
        if signal is not None:
            return None
        # Direct call (Control Room / API) — run full time-window scan
        return _emergence_run(**kwargs)

    # ── Engine registration ───────────────────────────────────────────────────
    conclave.register_engine("emergence_engine_engine", emergence_run)

    # ── Route registration ────────────────────────────────────────────────────
    def emergence_route():
        from flask import jsonify
        from forge_modules.emergence_engine.engine import query_emergence
        try:
            data = query_emergence()
            return jsonify({
                "emergence": data,
                "total":     len(data),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    emergence_route.__name__ = "api_intel_emergence"
    conclave.register_route(
        "/api/intel/emergence",
        emergence_route,
        methods=["GET"],
    )

    log.info(
        "[emergence_engine] Registered — "
        "engine: emergence_engine_engine (graph-level, no-op on per-signal calls) | "
        "route: GET /api/intel/emergence"
    )
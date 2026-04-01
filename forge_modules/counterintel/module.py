"""
counterintel — Module Entry Point  (v1.1)
══════════════════════════════════════════
Follows FORGE Pipeline Contracts:
  1. All imports inside register() — never at module level
  2. Only public run() imported from engine.py
  3. No side effects on module import

v1.1 fix: engine wrapper returns None immediately on per-signal Conclave
calls. counterintel is a corpus-level engine — it must only run when
called directly (Control Room / API), never on every ingested signal.
"""

from __future__ import annotations
import logging

log = logging.getLogger("forge.modules.counterintel")


def register(conclave) -> None:
    """
    Register counterintel into the Conclave context.
    All imports happen here — once, at registration time.
    """

    from forge_modules.counterintel.engine import run as _corpus_run

    # ── Engine wrapper — no-op on per-signal calls ────────────────────────────
    # Conclave calls registered engines with a signal dict on every ingest.
    # counterintel analyses the full corpus, not individual signals.
    # Returning None tells Conclave this engine has nothing to contribute
    # for this signal — no cost, no side effects.
    def counterintel_run(signal: dict = None, **kwargs):
        # Per-signal call from Conclave ingest — skip entirely
        if signal is not None:
            return None
        # Direct call (Control Room / API) — run full corpus scan
        return _corpus_run(**kwargs)

    # ── Engine registration ───────────────────────────────────────────────────
    conclave.register_engine("counterintel_engine", counterintel_run)

    # ── Route: GET /api/counterintel/flags ────────────────────────────────────
    def flags_route():
        from flask import jsonify, request
        from forge_modules.counterintel.engine import query_flags
        flag_type      = request.args.get("type", "").strip() or None
        min_confidence = float(request.args.get("min_confidence", 0.0))
        limit          = min(int(request.args.get("limit", 200)), 500)
        try:
            data = query_flags(
                flag_type=flag_type,
                min_confidence=min_confidence,
                limit=limit,
            )
            return jsonify({
                "flags":  data,
                "total":  len(data),
                "filter": {
                    "type":           flag_type,
                    "min_confidence": min_confidence,
                },
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    flags_route.__name__ = "api_counterintel_flags"
    conclave.register_route(
        "/api/counterintel/flags",
        flags_route,
        methods=["GET"],
    )

    # ── Route: GET /api/counterintel/summary ──────────────────────────────────
    def summary_route():
        from flask import jsonify
        from forge_modules.counterintel.engine import query_summary
        try:
            data = query_summary()
            return jsonify(data)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    summary_route.__name__ = "api_counterintel_summary"
    conclave.register_route(
        "/api/counterintel/summary",
        summary_route,
        methods=["GET"],
    )

    log.info(
        "[counterintel] Registered — "
        "engine: counterintel_engine (corpus-level, no-op on per-signal calls) | "
        "routes: /api/counterintel/flags, /api/counterintel/summary"
    )
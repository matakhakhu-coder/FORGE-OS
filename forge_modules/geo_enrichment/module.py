"""
geo_enrichment — Module Entry Point  (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Follows FORGE Pipeline Contracts (FORGE_PIPELINE_CONTRACTS.md):

    1. All imports inside register() — never inside hooks
    2. Only public run() imported from engine.py
    3. Hook captures function reference via closure
    4. No side effects on module import
"""

from __future__ import annotations
import logging

log = logging.getLogger("forge.modules.geo_enrichment")


def register(conclave) -> None:
    """
    Register geo_enrichment into the Conclave context.
    All imports happen here — once, at registration time.
    """

    # ── Single import — public interface only ─────────────────────────────────
    from forge_modules.geo_enrichment.engine import run as geo_run

    # ── Engine registration ───────────────────────────────────────────────────
    conclave.register_engine("geo_enrichment_engine", geo_run)

    # ── Hook: on_signal ───────────────────────────────────────────────────────
    # geo_run captured by closure — no imports at hook-fire time
    def on_signal(signal: dict) -> None:
        result = geo_run(signal)
        if result is not None:
            loc = result.provenance.get("strategic_location") or \
                  result.provenance.get("province", "SA")
            tier = result.provenance.get("location_tier", "")
            log.debug(
                f"[geo_enrichment] {signal.get('signal_id','?')[:8]}... "
                f"→ {loc} [{tier}] gravity={result.gravity}"
            )

    conclave.register_hook("on_signal", on_signal)

    log.info(
        "[geo_enrichment] Registered — "
        "engine: geo_enrichment_engine | hook: on_signal"
    )
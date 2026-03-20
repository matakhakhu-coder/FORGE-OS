"""
signal_enrichment — Module Entry Point  (v2.0 — Reference Standard)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This is the ONLY entry point the FMS loader calls.
register(conclave) is the entire public API of this module.

FMS Contract Rules enforced here:
    1. All imports happen ONCE inside register() — never inside hooks
    2. Only the public run() function is imported from engine.py
    3. Hook closures capture function references — no dynamic resolution
    4. No side effects on import of this file
"""

from __future__ import annotations
import logging

log = logging.getLogger("forge.modules.signal_enrichment")


def register(conclave) -> None:
    """
    Register the signal_enrichment module into the Conclave context.

    ALL imports happen here — once, at registration time.
    Hooks capture references via closure. They never import anything.
    """

    # ── Single import — only the public interface ─────────────────────────────
    from forge_modules.signal_enrichment.engine import run as engine_run

    # ── Engine registration ───────────────────────────────────────────────────
    conclave.register_engine("signal_enrichment_engine", engine_run)

    # ── Hook: on_signal ───────────────────────────────────────────────────────
    # engine_run is captured by closure at register() time.
    # No imports, no dynamic resolution, no filesystem access inside this hook.
    def on_signal(signal: dict) -> None:
        """
        Fires on every signal entering the pipeline.
        Calls engine_run and logs entity matches at DEBUG level.
        Zero imports at hook-fire time — reference captured at registration.
        """
        result = engine_run(signal)
        if result is not None and result.entities:
            log.debug(
                f"[signal_enrichment] {signal.get('signal_id', '?')[:8]}... "
                f"matched: {result.entities}"
            )

    conclave.register_hook("on_signal", on_signal)

    log.info(
        f"[signal_enrichment] Registered -- "
        f"engine: signal_enrichment_engine | hook: on_signal"
    )
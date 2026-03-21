"""
FORGE — FMS Bootstrap
━━━━━━━━━━━━━━━━━━━━━
Single function called once at startup (in mega_ingest.py or app.py).
Discovers all modules, loads them, logs the result.

Usage:
    from core.fms.bootstrap import bootstrap_fms
    bootstrap_fms()

After this call, get_context() returns the populated ConclaveContext
and the pipeline will automatically use registered module engines and hooks.
"""

from __future__ import annotations
import logging

log = logging.getLogger("forge.fms.bootstrap")


def bootstrap_fms(verbose: bool = True) -> dict:
    """
    Initialise the Forge Module System.

    1. Gets (or creates) the ConclaveContext singleton
    2. Discovers and loads all modules from forge_modules/
    3. Logs a status summary
    4. Returns the load summary dict

    Safe to call multiple times — modules already loaded are not reloaded.
    """
    from core.conclave.context import get_context
    from core.fms.loader import load_modules

    context = get_context()

    # Don't reload if already bootstrapped
    already_loaded = list(context.get_loaded_modules().keys())
    if already_loaded:
        log.info(f"[FMS] Already bootstrapped — {len(already_loaded)} module(s) active")
        return {"loaded": already_loaded, "rejected": []}

    summary = load_modules(context)

    if verbose:
        status = context.status()
        log.info(f"[FMS] Bootstrap complete:")
        log.info(f"  Modules  : {status['modules']}")
        log.info(f"  Engines  : {status['engines']}")
        log.info(f"  Hooks    : {status['hooks']}")
        log.info(f"  Routes   : {status['routes']}")

    return summary
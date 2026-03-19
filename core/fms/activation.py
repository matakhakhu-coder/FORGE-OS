"""
FORGE — FMS Activation Layer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bridges the gap between module readiness and pipeline attachment.

    READY  → validated, importable, not yet in Conclave
    ATTACHED → engines and hooks live in ConclaveContext

Two functions. That is the entire public API.

    attach_module(module_name, context)  → dict
    detach_module(module_name, context)  → dict

Design principles:
    - Reuses loader._import_module_file — no duplicate discovery logic
    - Reuses validator — fail-safe re-validation before attach
    - Idempotent — attaching an already-active module is a no-op
    - Isolated — failure never propagates to caller
    - Ownership-aware — detach only removes the named module's contributions
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any, Optional

log = logging.getLogger("forge.fms.activation")

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "forge_modules"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_module_dir(module_name: str,
                     modules_dir: Path = None) -> Optional[Path]:
    """
    Locate a module's directory by name.
    Checks both directory name and manifest name.
    Returns None if not found.
    """
    base = modules_dir or MODULES_DIR
    if not base.exists():
        return None

    for candidate in base.iterdir():
        if not candidate.is_dir() or candidate.name.startswith("_"):
            continue
        # Match by directory name
        if candidate.name == module_name:
            return candidate
        # Match by manifest name
        manifest_path = candidate / "manifest.json"
        if manifest_path.exists():
            try:
                import json
                manifest = json.load(open(manifest_path))
                if manifest.get("name") == module_name:
                    return candidate
            except Exception:
                pass
    return None


class _OwnerAwareContext:
    """
    Thin wrapper around ConclaveContext that injects _owner into
    register_engine() and register_hook() calls during attach.

    This means module code (register(conclave)) needs no changes —
    it calls conclave.register_engine() exactly as before, and
    ownership is tracked transparently.
    """

    def __init__(self, real_context, owner: str):
        self._ctx   = real_context
        self._owner = owner
        # Track what this module registers during attach
        self._registered_engines: list = []
        self._registered_hooks:   list = []

    def register_engine(self, name: str, fn) -> None:
        self._ctx.register_engine(name, fn, _owner=self._owner)
        self._registered_engines.append(name)

    def register_hook(self, hook_name: str, fn) -> None:
        self._ctx.register_hook(hook_name, fn, _owner=self._owner)
        if hook_name not in self._registered_hooks:
            self._registered_hooks.append(hook_name)

    def register_route(self, rule: str, view_func, methods=None) -> None:
        self._ctx.register_route(rule, view_func, methods)

    def register_module(self, name: str, manifest: dict) -> None:
        self._ctx.register_module(name, manifest)

    # Pass through everything else unchanged
    def __getattr__(self, name: str):
        return getattr(self._ctx, name)


# ── Public API ────────────────────────────────────────────────────────────────

def attach_module(
    module_name: str,
    context,
    modules_dir: Path = None,
) -> Dict[str, Any]:
    """
    Explicitly attach a READY module to Conclave.

    Steps:
        1. Check not already attached (idempotent)
        2. Locate module directory
        3. Re-validate manifest
        4. Re-import module.py
        5. Re-validate Python contract
        6. Call register() via ownership-aware wrapper
        7. Validate declared engines exist post-registration
        8. Mark module as active in context

    Returns:
        {
            "status":  "attached" | "already_active" | "failed",
            "module":  module_name,
            "engines": [engine_names, ...],
            "hooks":   [hook_names, ...],
            "reason":  str  (only on failure)
        }
    """
    # ── Step 1: idempotency ───────────────────────────────────────────────────
    if context.is_active(module_name):
        log.info(f"[FMS] attach: '{module_name}' already active — no-op")
        return {"status": "already_active", "module": module_name,
                "engines": [], "hooks": []}

    try:
        # ── Step 2: locate ────────────────────────────────────────────────────
        module_dir = _find_module_dir(module_name, modules_dir)
        if module_dir is None:
            return _fail(module_name, f"Module '{module_name}' not found in forge_modules/")

        # ── Step 3: re-validate manifest ──────────────────────────────────────
        from core.fms.validator import validate_manifest, validate_module_contract
        valid, reason, manifest = validate_manifest(module_dir / "manifest.json")
        if not valid:
            return _fail(module_name, f"Manifest invalid: {reason}")

        # ── Step 4: re-import module.py ───────────────────────────────────────
        from core.fms.loader import _import_module_file
        module_py = module_dir / "module.py"
        if not module_py.exists():
            return _fail(module_name, "module.py not found")

        mod = _import_module_file(module_name, module_py)

        # ── Step 5: re-validate contract ─────────────────────────────────────
        valid, reason = validate_module_contract(mod)
        if not valid:
            return _fail(module_name, f"Contract invalid: {reason}")

        # ── Step 6: register via ownership-aware wrapper ──────────────────────
        log.info(f"[FMS] Attaching '{module_name}' v{manifest['version']}...")
        wrapper = _OwnerAwareContext(context, owner=module_name)
        mod.register(wrapper)

        # ── Step 7: validate declared engines are present ─────────────────────
        declared_engines = manifest.get("engines", [])
        registered       = set(context.get_engines().keys())
        missing_engines  = [e for e in declared_engines if e not in registered]
        if missing_engines:
            # Clean up whatever was registered before failing
            for eng in wrapper._registered_engines:
                context.deregister_engine(eng)
            context.deregister_hooks_for_module(module_name)
            return _fail(module_name,
                         f"Declared engines not registered: {missing_engines}")

        # ── Step 8: mark active ───────────────────────────────────────────────
        context.register_module(module_name, manifest)
        context.mark_active(
            module_name,
            manifest,
            engines=wrapper._registered_engines,
            hooks=wrapper._registered_hooks,
        )

        log.info(
            f"[FMS] '{module_name}' attached — "
            f"engines: {wrapper._registered_engines}, "
            f"hooks: {wrapper._registered_hooks}"
        )

        return {
            "status":  "attached",
            "module":  module_name,
            "engines": wrapper._registered_engines,
            "hooks":   wrapper._registered_hooks,
        }

    except Exception as exc:
        log.error(f"[FMS] attach '{module_name}' fatal error: {exc}", exc_info=True)
        return _fail(module_name, str(exc))


def detach_module(
    module_name: str,
    context,
) -> Dict[str, Any]:
    """
    Safely remove a module's engines and hooks from Conclave.

    Does NOT affect:
        - Other modules' engines or hooks
        - The module's readiness state (it remains READY)
        - The _modules registry (it remains discovered)

    Returns:
        {
            "status":           "detached" | "not_active" | "failed",
            "module":           module_name,
            "engines_removed":  [engine_names, ...],
            "hooks_removed":    [hook_names, ...],
        }
    """
    if not context.is_active(module_name):
        log.info(f"[FMS] detach: '{module_name}' is not active — no-op")
        return {"status": "not_active", "module": module_name,
                "engines_removed": [], "hooks_removed": []}

    try:
        active_info     = context.get_active_modules().get(module_name, {})
        owned_engines   = active_info.get("engines", [])
        engines_removed = []
        hooks_removed   = []

        # Remove engines owned by this module
        for engine_name in owned_engines:
            if context.deregister_engine(engine_name):
                engines_removed.append(engine_name)

        # Remove hooks registered by this module
        hooks_removed = context.deregister_hooks_for_module(module_name)

        # Mark inactive
        context.mark_inactive(module_name)

        log.info(
            f"[FMS] '{module_name}' detached — "
            f"engines removed: {engines_removed}, "
            f"hooks removed: {hooks_removed}"
        )

        return {
            "status":          "detached",
            "module":          module_name,
            "engines_removed": engines_removed,
            "hooks_removed":   hooks_removed,
        }

    except Exception as exc:
        log.error(f"[FMS] detach '{module_name}' fatal error: {exc}", exc_info=True)
        return {
            "status": "failed",
            "module": module_name,
            "reason": str(exc),
            "engines_removed": [],
            "hooks_removed": [],
        }


def _fail(module_name: str, reason: str) -> Dict[str, Any]:
    log.warning(f"[FMS] attach '{module_name}' failed: {reason}")
    return {
        "status":  "failed",
        "module":  module_name,
        "reason":  reason,
        "engines": [],
        "hooks":   [],
    }
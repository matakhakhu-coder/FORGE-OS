"""
FORGE — ConclaveContext  (Activation Layer — FMS Phase 2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Extended from Phase 1 to support explicit module activation/detach.

Changes from Phase 1:
    + _active_modules   — tracks attached modules separately from discovered ones
    + _engine_owners    — maps engine name → owning module (enables safe detach)
    + _hook_owners      — maps hook name → [owning modules] (enables safe detach)
    + register_engine() — accepts optional _owner kwarg (no breaking change)
    + register_hook()   — accepts optional _owner kwarg (no breaking change)
    + mark_active()     — called by attach_module after successful registration
    + mark_inactive()   — called by detach_module
    + is_active()       — idempotency check
    + get_active_modules()
    + deregister_engine()
    + deregister_hooks_for_module()
    + status()          — extended with active_modules, active_engines, active_hooks

All Phase 1 callers continue to work unchanged.
"""

from __future__ import annotations
from typing import Callable, Dict, List, Any, Optional
import logging

log = logging.getLogger("forge.conclave.context")


class ConclaveContext:
    """
    Shared registration context for Forge Native Modules.

    Lifecycle:
        1. Created once at startup by the FMS loader
        2. Passed to each module's register(conclave) call
        3. Consumed by ingest.py for hook execution
        4. Consumed by run_conclave_with_modules() for engine results

    Phase 2 addition:
        5. attach_module() explicitly activates a READY module
        6. detach_module() safely removes it without affecting others
    """

    def __init__(self) -> None:
        # Registered module engines: name → callable(signal) → AnalysisResult | None
        self._engines:  Dict[str, Callable] = {}

        # Registered hooks: hook_name → [callable, ...]
        self._hooks:    Dict[str, List[Callable]] = {}

        # Registered Flask routes: [(rule, view_func, methods), ...]
        self._routes:   List[tuple] = []

        # Module metadata: name → manifest dict (all discovered)
        self._modules:  Dict[str, Dict[str, Any]] = {}

        # ── Activation tracking (Phase 2) ─────────────────────────────────────
        # Attached modules only: name → {manifest, engines, hooks}
        self._active_modules: Dict[str, Dict[str, Any]] = {}

        # Ownership maps for safe detach
        self._engine_owners: Dict[str, str]        = {}  # engine → module
        self._hook_owners:   Dict[str, List[str]]  = {}  # hook → [modules]

    # ── Engine registration ───────────────────────────────────────────────────

    def register_engine(self, name: str, fn: Callable,
                        _owner: str = None) -> None:
        """
        Register a module engine.
        fn signature: fn(signal: dict) -> AnalysisResult | None

        _owner: module name passed by attach_module for ownership tracking.
                Optional — existing callers without _owner continue to work.
        """
        if name in self._engines:
            log.debug(f"[FMS] Engine '{name}' already registered — overwriting")
        self._engines[name] = fn
        if _owner:
            self._engine_owners[name] = _owner
        log.info(f"[FMS] Engine registered: {name}")

    def get_engines(self) -> Dict[str, Callable]:
        return dict(self._engines)

    # ── Hook registration ─────────────────────────────────────────────────────

    def register_hook(self, hook_name: str, fn: Callable,
                      _owner: str = None) -> None:
        """
        Register a pipeline hook.

        Supported hooks:
            on_signal(signal: dict) -> None
            on_ingest(signal: dict, result: dict) -> None

        _owner: module name passed by attach_module for ownership tracking.
                Optional — existing callers without _owner continue to work.
        """
        if hook_name not in self._hooks:
            self._hooks[hook_name] = []
        self._hooks[hook_name].append(fn)
        if _owner:
            if hook_name not in self._hook_owners:
                self._hook_owners[hook_name] = []
            if _owner not in self._hook_owners[hook_name]:
                self._hook_owners[hook_name].append(_owner)
        log.info(f"[FMS] Hook registered: {hook_name} ← {fn.__module__}.{fn.__name__}")

    def fire_hook(self, hook_name: str, *args, **kwargs) -> None:
        """
        Fire all callables registered under hook_name.
        Failures in hooks are caught and logged — never propagated.
        """
        for fn in self._hooks.get(hook_name, []):
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                log.error(f"[FMS] Hook '{hook_name}' error in {fn.__module__}: {exc}")

    # ── Route registration ────────────────────────────────────────────────────

    def register_route(self, rule: str, view_func: Callable,
                       methods: Optional[List[str]] = None) -> None:
        """Register a Flask route from a module."""
        self._routes.append((rule, view_func, methods or ["GET"]))
        log.info(f"[FMS] Route registered: {rule}")

    def apply_routes(self, app) -> None:
        """Apply all registered module routes to a Flask app instance."""
        for rule, view_func, methods in self._routes:
            try:
                app.add_url_rule(rule, view_func.__name__, view_func, methods=methods)
                log.info(f"[FMS] Route applied: {rule}")
            except Exception as exc:
                log.error(f"[FMS] Route apply failed for {rule}: {exc}")

    # ── Module metadata ───────────────────────────────────────────────────────

    def register_module(self, name: str, manifest: Dict[str, Any]) -> None:
        """Record a discovered module. Does NOT mean it is active."""
        self._modules[name] = manifest

    def get_loaded_modules(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._modules)

    # ── Activation tracking (Phase 2) ─────────────────────────────────────────

    def mark_active(self, module_name: str, manifest: Dict[str, Any],
                    engines: List[str], hooks: List[str]) -> None:
        """
        Record a module as actively attached to Conclave.
        Called by attach_module() after successful register().
        """
        self._active_modules[module_name] = {
            "manifest": manifest,
            "engines":  list(engines),
            "hooks":    list(hooks),
        }
        log.info(f"[FMS] Module marked active: {module_name}")

    def mark_inactive(self, module_name: str) -> None:
        """Remove a module from the active registry. Called by detach_module()."""
        self._active_modules.pop(module_name, None)

    def is_active(self, module_name: str) -> bool:
        """Return True if the module is currently attached to Conclave."""
        return module_name in self._active_modules

    def get_active_modules(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._active_modules)

    # ── Deregistration (Phase 2 — used by detach_module) ─────────────────────

    def deregister_engine(self, name: str) -> bool:
        """
        Remove an engine from the context.
        Returns True if removed, False if it didn't exist.
        Only removes engines owned by a module — never core engines.
        """
        if name in self._engines:
            del self._engines[name]
            self._engine_owners.pop(name, None)
            log.info(f"[FMS] Engine deregistered: {name}")
            return True
        return False

    def deregister_hooks_for_module(self, module_name: str) -> List[str]:
        """
        Remove all hook callables registered by a specific module.
        Identified by callable's __module__ attribute containing module_name.
        Returns list of hook_names that had entries removed.
        Does NOT affect hooks from other modules.
        """
        removed = []
        for hook_name in list(self._hooks.keys()):
            before = len(self._hooks[hook_name])
            self._hooks[hook_name] = [
                fn for fn in self._hooks[hook_name]
                if not (
                    hasattr(fn, "__module__") and
                    module_name in (fn.__module__ or "")
                )
            ]
            after = len(self._hooks[hook_name])
            if before != after:
                removed.append(hook_name)
                log.info(
                    f"[FMS] Hook '{hook_name}': removed {before - after} "
                    f"callable(s) from module '{module_name}'"
                )
            # Clean up owner record
            if hook_name in self._hook_owners:
                try:
                    self._hook_owners[hook_name].remove(module_name)
                except ValueError:
                    pass
        return removed

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            # Discovery layer
            "modules":        list(self._modules.keys()),
            # Activation layer
            "active_modules": list(self._active_modules.keys()),
            "active_engines": [
                e for e, owner in self._engine_owners.items()
                if owner in self._active_modules
            ],
            "active_hooks": {
                k: len([o for o in v if o in self._active_modules])
                for k, v in self._hook_owners.items()
            },
            # Raw counts (all registered, active or not)
            "engines": list(self._engines.keys()),
            "hooks":   {k: len(v) for k, v in self._hooks.items()},
            "routes":  [r[0] for r in self._routes],
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_context: Optional[ConclaveContext] = None


def get_context() -> ConclaveContext:
    global _context
    if _context is None:
        _context = ConclaveContext()
    return _context


def reset_context() -> None:
    """For testing only — resets the singleton."""
    global _context
    _context = None
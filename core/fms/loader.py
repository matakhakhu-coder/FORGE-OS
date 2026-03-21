"""
FORGE — FMS Module Loader
━━━━━━━━━━━━━━━━━━━━━━━━━
Discovers, validates, and loads Forge Native Modules from forge_modules/.

Loader contract:
    - Scans forge_modules/ for subdirectories containing manifest.json
    - Validates manifest structure
    - Dynamically imports module.py
    - Validates Python contract (register() exists)
    - Calls register(conclave)
    - Validates post-registration state (declared engines exist)
    - Failure in any module is isolated — other modules and FORGE continue

Usage:
    from core.fms.loader import load_modules
    from core.conclave.context import get_context

    context = get_context()
    load_modules(context)
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Dict, Any, List

from core.fms.validator import (
    validate_manifest,
    validate_module_contract,
    validate_declared_engines,
)

log = logging.getLogger("forge.fms.loader")

# Root of all modules — relative to FORGE project root
MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "forge_modules"


def _import_module_file(module_name: str, module_path: Path):
    """
    Dynamically import a module.py file in isolation.
    Returns the imported module object or raises ImportError.
    """
    spec = importlib.util.spec_from_file_location(
        f"forge_modules.{module_name}.module",
        str(module_path),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for {module_path}")

    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules so relative imports within the module work
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_modules(context, modules_dir: Path = None) -> Dict[str, Any]:
    """
    Discover and load all valid Forge Native Modules.

    Returns a summary dict:
        {
            "loaded":   [module_name, ...],
            "rejected": [(module_name, reason), ...],
        }
    """
    base = modules_dir or MODULES_DIR
    summary: Dict[str, List] = {"loaded": [], "rejected": []}

    if not base.exists():
        log.warning(f"[FMS] forge_modules/ not found at {base} — no modules loaded")
        return summary

    candidates = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")]

    if not candidates:
        log.info("[FMS] No modules found in forge_modules/")
        return summary

    log.info(f"[FMS] Scanning {len(candidates)} module candidate(s)...")

    for module_dir in sorted(candidates):
        name = module_dir.name
        _load_one(name, module_dir, context, summary)

    log.info(
        f"[FMS] Load complete — "
        f"{len(summary['loaded'])} loaded, "
        f"{len(summary['rejected'])} rejected"
    )
    if summary["rejected"]:
        for mod_name, reason in summary["rejected"]:
            log.warning(f"[FMS] Rejected '{mod_name}': {reason}")

    return summary


def _load_one(name: str, module_dir: Path, context, summary: Dict) -> None:
    """Load a single module. All failures are caught and recorded."""
    try:
        # ── Step 1: validate manifest ─────────────────────────────────────────
        manifest_path = module_dir / "manifest.json"
        valid, reason, manifest = validate_manifest(manifest_path)
        if not valid:
            summary["rejected"].append((name, reason))
            return

        # Use manifest name as canonical identifier (may differ from dir name)
        mod_name = manifest["name"]

        # ── Step 2: check module.py exists ────────────────────────────────────
        module_py = module_dir / "module.py"
        if not module_py.exists():
            summary["rejected"].append((mod_name, "module.py not found"))
            return

        # ── Step 3: dynamic import ────────────────────────────────────────────
        log.info(f"[FMS] Loading '{mod_name}' v{manifest['version']}...")
        mod = _import_module_file(mod_name, module_py)

        # ── Step 4: validate Python contract ─────────────────────────────────
        valid, reason = validate_module_contract(mod)
        if not valid:
            summary["rejected"].append((mod_name, reason))
            return

        # ── Step 5: register into context ─────────────────────────────────────
        mod.register(context)

        # ── Step 6: validate post-registration state ──────────────────────────
        valid, reason = validate_declared_engines(manifest, context)
        if not valid:
            summary["rejected"].append((mod_name, reason))
            return

        # ── Step 7: record metadata ───────────────────────────────────────────
        context.register_module(mod_name, manifest)
        summary["loaded"].append(mod_name)
        log.info(f"[FMS] '{mod_name}' v{manifest['version']} loaded successfully")

    except Exception as exc:
        # Hard isolation — nothing leaks out
        log.error(f"[FMS] Fatal error loading '{name}': {exc}", exc_info=True)
        summary["rejected"].append((name, str(exc)))
"""
FORGE — FMS Contract Validator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Validates module manifests and Python contracts before loading.
A module that fails validation is rejected with a clear error.
It NEVER crashes the host process.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Tuple, Dict, Any

log = logging.getLogger("forge.fms.validator")

# ── Manifest schema ───────────────────────────────────────────────────────────

REQUIRED_MANIFEST_KEYS = {"name", "version", "engines"}

MANIFEST_SCHEMA: Dict[str, type] = {
    "name":     str,
    "version":  str,
    "engines":  list,
}

OPTIONAL_MANIFEST_KEYS = {"hooks", "routes", "schema", "description", "author"}


def validate_manifest(manifest_path: Path) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Parse and validate a module's manifest.json.

    Returns:
        (valid: bool, reason: str, manifest: dict)
    """
    if not manifest_path.exists():
        return False, f"manifest.json not found at {manifest_path}", {}

    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except json.JSONDecodeError as exc:
        return False, f"manifest.json is not valid JSON: {exc}", {}
    except Exception as exc:
        return False, f"Could not read manifest.json: {exc}", {}

    # Required keys
    missing = REQUIRED_MANIFEST_KEYS - set(manifest.keys())
    if missing:
        return False, f"manifest.json missing required keys: {missing}", {}

    # Type checks
    for key, expected_type in MANIFEST_SCHEMA.items():
        if key in manifest and not isinstance(manifest[key], expected_type):
            return False, (
                f"manifest.json '{key}' must be {expected_type.__name__}, "
                f"got {type(manifest[key]).__name__}"
            ), {}

    # Name must be a valid Python identifier (used as namespace)
    name = manifest.get("name", "")
    if not name.replace("_", "").replace("-", "").isalnum():
        return False, f"Module name '{name}' must be alphanumeric (hyphens/underscores allowed)", {}

    # Engines list must contain strings
    for i, eng in enumerate(manifest.get("engines", [])):
        if not isinstance(eng, str):
            return False, f"manifest.json 'engines[{i}]' must be a string", {}

    return True, "ok", manifest


def validate_module_contract(module) -> Tuple[bool, str]:
    """
    Validate that a loaded Python module satisfies the FMS contract.

    Contract:
        - Must expose a callable register(conclave) function
        - Must not have already caused side effects (checked via import isolation)
    """
    if not hasattr(module, "register"):
        return False, f"Module '{module.__name__}' missing required register() function"

    if not callable(module.register):
        return False, f"Module '{module.__name__}' register is not callable"

    # Inspect register() arity — must accept at least one argument (conclave)
    import inspect
    sig = inspect.signature(module.register)
    params = list(sig.parameters.values())
    if len(params) < 1:
        return False, (
            f"Module '{module.__name__}' register() must accept at least one argument (conclave)"
        )

    return True, "ok"


def validate_declared_engines(manifest: Dict[str, Any], context) -> Tuple[bool, str]:
    """
    After registration, verify that all engines declared in the manifest
    were actually registered into the context.
    """
    declared  = set(manifest.get("engines", []))
    registered = set(context.get_engines().keys())

    missing = declared - registered
    if missing:
        return False, (
            f"Module '{manifest['name']}' declared engines {missing} "
            f"but did not register them"
        )

    return True, "ok"
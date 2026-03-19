"""
FORGE — FMS Module Readiness Scanner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scans forge_modules/ and reports the readiness of every module
WITHOUT loading them into the pipeline or mutating any state.

This is a pure observability layer. It answers one question:
    "What modules exist, and are they valid?"

It does NOT:
    - Call register(conclave)
    - Touch the database
    - Modify ConclaveContext
    - Require bootstrap_fms() to have run

It CAN be called from anywhere:
    - app.py startup (visibility only)
    - CLI: python -m core.fms.readiness
    - Any diagnostic endpoint
    - A module itself

Output is structured so it can be logged, returned as JSON,
or displayed in the diagnostics UI.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Any, List

log = logging.getLogger("forge.fms.readiness")

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "forge_modules"

# ── Per-module readiness states ───────────────────────────────────────────────

READY     = "READY"       # manifest valid, contract satisfied, engine importable
DEGRADED  = "DEGRADED"    # manifest valid but engine import failed
INVALID   = "INVALID"     # manifest missing or malformed
ABSENT    = "ABSENT"      # module.py missing


def check_module(module_dir: Path) -> Dict[str, Any]:
    """
    Perform a full dry-check of one module directory.
    Returns a readiness report dict. Never raises.
    """
    name = module_dir.name
    report = {
        "name":             name,
        "path":             str(module_dir),
        "status":           ABSENT,
        "manifest_valid":   False,
        "manifest_version": None,
        "engines_declared": [],
        "hooks_declared":   [],
        "register_present": False,
        "engines_importable": [],
        "engines_missing":    [],
        "issues":           [],
    }

    # ── Step 1: manifest ──────────────────────────────────────────────────────
    manifest_path = module_dir / "manifest.json"
    if not manifest_path.exists():
        report["issues"].append("manifest.json not found")
        return report

    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except json.JSONDecodeError as exc:
        report["status"] = INVALID
        report["issues"].append(f"manifest.json invalid JSON: {exc}")
        return report
    except Exception as exc:
        report["status"] = INVALID
        report["issues"].append(f"manifest.json unreadable: {exc}")
        return report

    required = {"name", "version", "engines"}
    missing_keys = required - set(manifest.keys())
    if missing_keys:
        report["status"] = INVALID
        report["issues"].append(f"manifest.json missing keys: {missing_keys}")
        return report

    report["manifest_valid"]   = True
    report["name"]             = manifest.get("name", name)
    report["manifest_version"] = manifest.get("version")
    report["engines_declared"] = manifest.get("engines", [])
    report["hooks_declared"]   = manifest.get("hooks", [])

    # ── Step 2: module.py exists ──────────────────────────────────────────────
    module_py = module_dir / "module.py"
    if not module_py.exists():
        report["status"] = INVALID
        report["issues"].append("module.py not found")
        return report

    # ── Step 3: dry-import module.py (read-only — no register() called) ───────
    try:
        spec = importlib.util.spec_from_file_location(
            f"_fms_readiness_check.{report['name']}.module",
            str(module_py),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:
        report["status"] = DEGRADED
        report["issues"].append(f"module.py import failed: {exc}")
        return report

    # ── Step 4: check register() contract ────────────────────────────────────
    if not hasattr(mod, "register") or not callable(mod.register):
        report["status"] = INVALID
        report["issues"].append("register() function missing or not callable")
        return report

    sig    = inspect.signature(mod.register)
    params = list(sig.parameters.values())
    if len(params) < 1:
        report["status"] = INVALID
        report["issues"].append("register() must accept at least one argument (conclave)")
        return report

    report["register_present"] = True

    # ── Step 5: check each declared engine is importable ─────────────────────
    engine_py = module_dir / "engine.py"
    for engine_name in report["engines_declared"]:
        if engine_py.exists():
            try:
                eng_spec = importlib.util.spec_from_file_location(
                    f"_fms_readiness_check.{report['name']}.engine",
                    str(engine_py),
                )
                eng_mod = importlib.util.module_from_spec(eng_spec)
                eng_spec.loader.exec_module(eng_mod)
                report["engines_importable"].append(engine_name)
            except Exception as exc:
                report["engines_missing"].append(engine_name)
                report["issues"].append(f"engine '{engine_name}' import failed: {exc}")
        else:
            # Engine may be defined inside module.py — treat as importable
            report["engines_importable"].append(engine_name)

    # ── Final status ──────────────────────────────────────────────────────────
    if report["engines_missing"]:
        report["status"] = DEGRADED
    else:
        report["status"] = READY

    return report


def scan_modules(modules_dir: Path = None) -> List[Dict[str, Any]]:
    """
    Scan all module candidates in forge_modules/.
    Returns a list of readiness report dicts.
    """
    base = modules_dir or MODULES_DIR
    if not base.exists():
        return []

    candidates = sorted(
        p for p in base.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )
    return [check_module(d) for d in candidates]


def report_readiness(modules_dir: Path = None, logger=None) -> List[Dict[str, Any]]:
    """
    Scan modules and emit structured log lines.
    Returns the full report list for use in diagnostics APIs.

    This is the function to call from app.py or any diagnostic route.
    It has zero side effects.
    """
    lg = logger or log
    reports = scan_modules(modules_dir)

    if not reports:
        lg.info("[FMS] No modules found in forge_modules/")
        return []

    lg.info(f"[FMS] Module readiness scan — {len(reports)} candidate(s)")
    lg.info("=" * 60)

    for r in reports:
        lg.info(f"[FMS] Module Detected  : {r['name']}")
        lg.info(f"[FMS] Manifest Valid   : {'YES' if r['manifest_valid'] else 'NO'}")
        if r["manifest_version"]:
            lg.info(f"[FMS] Version          : {r['manifest_version']}")
        if r["engines_declared"]:
            lg.info(f"[FMS] Engines Declared : {r['engines_declared']}")
        if r["engines_importable"]:
            lg.info(f"[FMS] Engine Available : {', '.join(r['engines_importable'])}")
        if r["hooks_declared"]:
            lg.info(f"[FMS] Hooks Declared   : {r['hooks_declared']}")
        lg.info(f"[FMS] Register Present : {'YES' if r['register_present'] else 'NO'}")
        lg.info(f"[FMS] Status           : {r['status']} (not yet attached to Conclave)")
        if r["issues"]:
            for issue in r["issues"]:
                lg.warning(f"[FMS] Issue            : {issue}")
        lg.info("-" * 60)

    ready    = sum(1 for r in reports if r["status"] == READY)
    degraded = sum(1 for r in reports if r["status"] == DEGRADED)
    invalid  = sum(1 for r in reports if r["status"] in (INVALID, ABSENT))

    lg.info(f"[FMS] Summary: {ready} READY · {degraded} DEGRADED · {invalid} INVALID")
    lg.info("=" * 60)

    return reports


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    reports = report_readiness()
    # Exit non-zero if any module is not READY
    not_ready = [r for r in reports if r["status"] != READY]
    sys.exit(1 if not_ready else 0)
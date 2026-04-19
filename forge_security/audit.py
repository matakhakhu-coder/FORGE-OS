"""
forge_security.audit — Dependency Audit & PowerShell History Forensics
=======================================================================
Two responsibilities:

  run_pip_audit()
      Shells out to `pip-audit` using a strict list-based subprocess call
      (never shell=True).  Returns an AuditResult dataclass with vulnerability
      counts and the raw JSON report.  Writes the report to
      logs/pip_audit_<timestamp>.json.

  dump_ps_history(out_path)
      Reads the PowerShell ConsoleHost_history.txt file **directly** from
      disk — no PowerShell process is spawned.  This is AMSI-safe: no
      heredoc, no stdin piping, no eval().  The raw history lines are
      written to *out_path* for manual analyst review.

Dependencies: pip-audit (pip install pip-audit)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default output directory for audit reports
_LOGS_DIR = Path("logs")

# PowerShell history file path (Windows default, user-relative)
_PS_HISTORY_PATH = (
    Path.home()
    / "AppData" / "Roaming" / "Microsoft" / "Windows"
    / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt"
)


@dataclass
class AuditResult:
    """Result of a pip-audit run."""
    timestamp: str
    vulnerable_count: int
    total_packages: int
    vulnerabilities: list[dict[str, Any]] = field(default_factory=list)
    raw_json: dict[str, Any] = field(default_factory=dict)
    report_path: Path | None = None
    error: str | None = None

    @property
    def clean(self) -> bool:
        return self.vulnerable_count == 0 and self.error is None


def run_pip_audit(
    *,
    output_dir: str | Path = _LOGS_DIR,
    timeout: int = 120,
) -> AuditResult:
    """
    Run pip-audit and return a structured result.

    Uses ``subprocess.run`` with a list-based argument vector — never
    ``shell=True``.  Writes a timestamped JSON report to *output_dir*.

    Parameters
    ----------
    output_dir : directory for report files (created if missing)
    timeout    : seconds before the audit subprocess is killed

    Returns
    -------
    AuditResult — check .clean for pass/fail, .vulnerabilities for details.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"pip_audit_{ts}.json"

    # Strict list-based command — no shell=True, no string interpolation
    cmd: list[str] = [
        sys.executable, "-m", "pip_audit",
        "--format", "json",
        "--output", str(report_path),
        "--progress-spinner", "off",
    ]

    logger.info("AUDIT | running pip-audit → %s", report_path)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,  # explicit — never True
        )
    except FileNotFoundError:
        err = (
            "pip-audit not found. Install it: pip install pip-audit"
        )
        logger.error("AUDIT | %s", err)
        return AuditResult(timestamp=ts, vulnerable_count=0, total_packages=0, error=err)
    except subprocess.TimeoutExpired:
        err = f"pip-audit timed out after {timeout}s"
        logger.error("AUDIT | %s", err)
        return AuditResult(timestamp=ts, vulnerable_count=0, total_packages=0, error=err)

    # pip-audit exits 1 when vulnerabilities are found — that's expected
    if proc.returncode not in (0, 1):
        err = f"pip-audit exited {proc.returncode}: {proc.stderr[:500]}"
        logger.error("AUDIT | %s", err)
        return AuditResult(timestamp=ts, vulnerable_count=0, total_packages=0, error=err)

    # Parse the JSON report
    raw: dict[str, Any] = {}
    if report_path.exists():
        try:
            raw = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("AUDIT | could not parse report JSON: %s", exc)

    # pip-audit JSON schema: {"dependencies": [{"name":..,"version":..,"vulns":[..]}]}
    dependencies: list[dict[str, Any]] = raw.get("dependencies", [])
    total = len(dependencies)
    vulns: list[dict[str, Any]] = []
    for dep in dependencies:
        for v in dep.get("vulns", []):
            vulns.append({
                "package": dep.get("name"),
                "version": dep.get("version"),
                "id": v.get("id"),
                "description": v.get("description", ""),
                "fix_versions": v.get("fix_versions", []),
            })

    result = AuditResult(
        timestamp=ts,
        vulnerable_count=len(vulns),
        total_packages=total,
        vulnerabilities=vulns,
        raw_json=raw,
        report_path=report_path,
    )

    if result.clean:
        logger.info("AUDIT | CLEAN — %d packages, 0 vulnerabilities", total)
    else:
        logger.warning(
            "AUDIT | %d VULNERABILITIES across %d packages — see %s",
            len(vulns), total, report_path,
        )
        for v in vulns:
            logger.warning(
                "  [%s] %s==%s — fix: %s",
                v["id"], v["package"], v["version"],
                ", ".join(v["fix_versions"]) or "no fix yet",
            )

    return result


def dump_ps_history(
    out_path: str | Path,
    *,
    ps_history_path: str | Path = _PS_HISTORY_PATH,
) -> int:
    """
    Read the PowerShell ConsoleHost_history.txt file directly from disk
    and write a copy to *out_path*.

    This function **never spawns a PowerShell process** — it opens the file
    as a regular text file.  This is AMSI-safe: no heredoc, no stdin piping,
    no eval(), no Get-Content call.

    Parameters
    ----------
    out_path         : destination file for the history dump
    ps_history_path  : override if the history file is in a non-standard location

    Returns
    -------
    Number of history lines written.  Returns 0 if the history file does
    not exist (e.g. PSReadLine not installed or no history yet).
    """
    src = Path(ps_history_path)
    dst = Path(out_path)

    if not src.exists():
        logger.info("AUDIT | PS history file not found at %s — skipping", src)
        return 0

    try:
        lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    except PermissionError as exc:
        logger.warning("AUDIT | cannot read PS history: %s", exc)
        return 0

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Write with ISO timestamp header for analyst context
    ts = datetime.now(timezone.utc).isoformat()
    header_lines = [
        f"# FORGE PowerShell History Forensic Dump",
        f"# Generated: {ts}",
        f"# Source:    {src}",
        f"# Lines:     {len(lines)}",
        "#",
        "",
    ]

    dst.write_text(
        "\n".join(header_lines + lines),
        encoding="utf-8",
    )

    logger.info("AUDIT | PS history dumped: %d lines → %s", len(lines), dst)
    return len(lines)


def run_full_audit(
    *,
    output_dir: str | Path = _LOGS_DIR,
) -> dict[str, Any]:
    """
    Convenience wrapper: run pip-audit AND dump PS history in one call.

    Returns a summary dict suitable for logging or display.
    """
    out_dir = Path(output_dir)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    audit = run_pip_audit(output_dir=out_dir)

    ps_out = out_dir / f"ps_history_{ts}.txt"
    ps_lines = dump_ps_history(ps_out)

    return {
        "audit_clean": audit.clean,
        "vulnerable_packages": audit.vulnerable_count,
        "total_packages": audit.total_packages,
        "vulnerabilities": audit.vulnerabilities,
        "report_path": str(audit.report_path) if audit.report_path else None,
        "ps_history_lines": ps_lines,
        "ps_history_path": str(ps_out) if ps_lines else None,
        "error": audit.error,
    }

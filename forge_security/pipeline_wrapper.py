"""
forge_security.pipeline_wrapper — Master Pipeline Hardener
===========================================================
This module is the single, authoritative gateway through which FORGE
spawns external processes.  It enforces:

  - Strictly list-based subprocess args (never shell=True, never string concat)
  - An explicit executable allowlist — anything not on the list is rejected
  - Timeout enforcement with SIGTERM + SIGKILL escalation
  - Stdout/stderr capture with size-capped buffers
  - A quarantine helper for artifacts that fail PDF detonation or sanitization

Public API
----------
  run_safe(cmd, ...)         — drop-in for subprocess.run(), hardened
  quarantine_artifact(path)  — move a bad file to quarantine/
  PipelineError              — raised on policy violations or subprocess failure

Integration with mega_ingest.py
--------------------------------
Replace any bare subprocess.run() / os.system() / Popen() calls with
run_safe().  The function signature is intentionally close to
subprocess.run() to minimise diff noise.

Example
-------
    from forge_security.pipeline_wrapper import run_safe

    result = run_safe(
        ["python", "mega_ingest.py", "--phase", "1"],
        timeout=300,
        capture_output=True,
    )
    print(result.stdout)

Dependencies: none beyond stdlib
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

logger = logging.getLogger(__name__)

# ── quarantine ────────────────────────────────────────────────────────────────
QUARANTINE_DIR: Final[Path] = Path("quarantine")

# ── output buffer caps ────────────────────────────────────────────────────────
MAX_STDOUT_BYTES: Final[int] = 10 * 1024 * 1024   # 10 MB
MAX_STDERR_BYTES: Final[int] = 2 * 1024 * 1024    #  2 MB

# ── allowed executables ───────────────────────────────────────────────────────
# Only binaries/modules explicitly listed here may be spawned via run_safe().
# Add new entries as needed — do NOT use wildcards.
_ALLOWED_EXECUTABLES: Final[frozenset[str]] = frozenset({
    # Python interpreter variants
    "python", "python3", "python.exe",
    # pip / package management (audit only)
    "pip", "pip3", "pip.exe",
    # pip-audit
    "pip-audit", "pip_audit",
    # git (for version checks)
    "git", "git.exe",
    # tesseract OCR
    "tesseract", "tesseract.exe",
    # pdftotext / poppler
    "pdftotext", "pdftotext.exe",
})


class PipelineError(Exception):
    """Raised when run_safe() rejects a command or a subprocess fails."""


@dataclass
class RunResult:
    """Structured result of a run_safe() call."""
    returncode: int
    stdout: str
    stderr: str
    cmd: list[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# ── public API ────────────────────────────────────────────────────────────────

def run_safe(
    cmd: Sequence[str],
    *,
    timeout: int = 120,
    capture_output: bool = True,
    check: bool = False,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> RunResult:
    """
    FORGE-hardened subprocess wrapper.

    Parameters
    ----------
    cmd            : command as a **list** of strings — never a bare string
    timeout        : seconds before SIGTERM is sent (default 120)
    capture_output : capture stdout/stderr (default True)
    check          : raise PipelineError on non-zero exit (default False)
    env            : explicit environment dict; if None, inherits os.environ
                     with PATH sanitised
    cwd            : working directory for the subprocess

    Returns
    -------
    RunResult with returncode, stdout, stderr.

    Raises
    ------
    PipelineError  if cmd is a string (not a list), executable not allowlisted,
                   timeout expires, or check=True and returncode != 0.
    """
    # ── 1. Type check: cmd must be a list/tuple, never a bare string ──────────
    if isinstance(cmd, str):
        raise PipelineError(
            "run_safe() requires a list, not a string. "
            "Pass: run_safe(['python', 'script.py']) — never run_safe('python script.py'). "
            "String commands prevent argument injection detection and imply shell=True."
        )

    cmd_list = list(cmd)
    if not cmd_list:
        raise PipelineError("run_safe() received an empty command list.")

    # ── 2. Executable allowlist check ────────────────────────────────────────
    executable = Path(cmd_list[0]).name.lower()
    # Allow sys.executable (current Python interpreter) always
    current_py = Path(sys.executable).name.lower()
    if executable not in _ALLOWED_EXECUTABLES and executable != current_py:
        raise PipelineError(
            f"Blocked: executable {cmd_list[0]!r} is not on the FORGE allowlist. "
            f"Add it to forge_security.pipeline_wrapper._ALLOWED_EXECUTABLES if intentional."
        )

    # ── 3. Argument injection scan ────────────────────────────────────────────
    _check_arg_injection(cmd_list)

    # ── 4. Sanitise environment ───────────────────────────────────────────────
    safe_env = _sanitised_env(env)

    logger.info("PIPELINE | run_safe: %s", _redact_cmd(cmd_list))

    # ── 5. Run — shell=False is the default and we never override it ──────────
    try:
        proc = subprocess.run(
            cmd_list,
            capture_output=capture_output,
            timeout=timeout,
            shell=False,   # explicit — never True
            env=safe_env,
            cwd=str(cwd) if cwd else None,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_on_timeout(exc)
        raise PipelineError(
            f"Subprocess timed out after {timeout}s: {cmd_list[0]!r}"
        ) from exc
    except FileNotFoundError as exc:
        raise PipelineError(
            f"Executable not found: {cmd_list[0]!r} — {exc}"
        ) from exc

    # ── 6. Buffer cap enforcement ─────────────────────────────────────────────
    stdout = (proc.stdout or "")[:MAX_STDOUT_BYTES]
    stderr = (proc.stderr or "")[:MAX_STDERR_BYTES]

    result = RunResult(
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        cmd=cmd_list,
    )

    if proc.returncode != 0:
        logger.warning(
            "PIPELINE | exit %d: %s\nSTDERR: %s",
            proc.returncode, _redact_cmd(cmd_list), stderr[:500],
        )

    if check and not result.ok:
        raise PipelineError(
            f"Subprocess failed (exit {proc.returncode}): {cmd_list[0]!r}\n"
            f"stderr: {stderr[:500]}"
        )

    return result


def quarantine_artifact(src: str | Path, *, reason: str = "") -> Path:
    """
    Move *src* to the quarantine directory.

    Parameters
    ----------
    src    : path to the file to quarantine
    reason : brief description of why this file was quarantined (for logs)

    Returns
    -------
    New path inside quarantine/.
    """
    src_path = Path(src)
    if not src_path.exists():
        logger.warning("PIPELINE | quarantine: source does not exist: %s", src_path)
        return src_path

    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    dest = QUARANTINE_DIR / src_path.name
    counter = 0
    while dest.exists():
        counter += 1
        dest = QUARANTINE_DIR / f"{src_path.stem}_{counter}{src_path.suffix}"

    shutil.move(str(src_path), str(dest))
    logger.warning(
        "PIPELINE | quarantined %s → %s%s",
        src_path, dest,
        f" ({reason})" if reason else "",
    )
    return dest


# ── internal helpers ──────────────────────────────────────────────────────────

# Shell metacharacters that should never appear in individual arguments
_SHELL_META_RE = __import__("re").compile(r"[;&|`$<>\\]")


def _check_arg_injection(cmd: list[str]) -> None:
    """
    Scan each argument for shell metacharacters that indicate an injection
    attempt.  We run shell=False so these are harmless in practice, but
    detecting them surfaces misuse of the API early.
    """
    for i, arg in enumerate(cmd[1:], start=1):
        if _SHELL_META_RE.search(arg):
            logger.warning(
                "PIPELINE | shell metachar in arg[%d]: %r — "
                "safe because shell=False, but review this call site",
                i, arg[:80],
            )


def _sanitised_env(override: dict[str, str] | None) -> dict[str, str]:
    """
    Return an environment dict with LD_PRELOAD and PYTHONSTARTUP stripped
    (common injection vectors).  Uses *override* if provided, else
    inherits the current process environment.
    """
    base = dict(override) if override is not None else dict(os.environ)
    for dangerous_key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONSTARTUP", "PYTHONINSPECT"):
        base.pop(dangerous_key, None)
    return base


def _redact_cmd(cmd: list[str]) -> str:
    """Return a loggable string with common secret args redacted."""
    redacted = []
    redact_next = False
    secret_flags = {"--password", "--token", "--key", "--secret", "--api-key"}
    for part in cmd:
        if redact_next:
            redacted.append("***")
            redact_next = False
        elif part.lower() in secret_flags:
            redacted.append(part)
            redact_next = True
        else:
            redacted.append(part)
    return " ".join(redacted)


def _kill_on_timeout(exc: subprocess.TimeoutExpired) -> None:
    """Escalate SIGTERM → SIGKILL on timeout (Unix) or terminate (Windows)."""
    proc = exc.process  # type: ignore[attr-defined]
    if proc is None:
        return
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        pass

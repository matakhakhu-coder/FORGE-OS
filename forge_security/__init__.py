"""
FORGE Security Manifest — forge_security package
=================================================
Four production-ready security modules:

  detonator        — PDF air-lock (magic-byte check, metadata strip, limits)
  sanitizer        — bleach-based XSS/injection prevention for scraped signals
  audit            — pip-audit supply-chain scanner + PowerShell history forensics
  pipeline_wrapper — master subprocess hardener with quarantine logic
"""

from .detonator import detonate_pdf, DetonationError
from .sanitizer import sanitize_signal_text, sanitize_html_fragment, SanitizationError
from .audit import run_pip_audit, dump_ps_history, AuditResult
from .pipeline_wrapper import run_safe, PipelineError

__all__ = [
    "detonate_pdf", "DetonationError",
    "sanitize_signal_text", "sanitize_html_fragment", "SanitizationError",
    "run_pip_audit", "dump_ps_history", "AuditResult",
    "run_safe", "PipelineError",
]

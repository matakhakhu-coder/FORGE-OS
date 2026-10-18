"""Regression tests for the fixes on the fix/audit-patches branch."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_artifact_processor_import_keeps_stdout_object():
    """Importing artifact_processor must not replace sys.stdout/sys.stderr.

    Replacing them with a new TextIOWrapper closed the shared buffer and
    stopped a plain `pytest` run with 'I/O operation on closed file'.
    """
    import forage.processors.artifact_processor as ap

    before_out, before_err = sys.stdout, sys.stderr
    importlib.reload(ap)
    assert sys.stdout is before_out
    assert sys.stderr is before_err

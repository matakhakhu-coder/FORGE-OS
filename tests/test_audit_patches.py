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


def _fresh_db(tmp_path, monkeypatch):
    """Create a fresh database the way the README does: --init-db, --migrate."""
    import app as forge_app

    db_file = tmp_path / "database.db"
    monkeypatch.setattr(forge_app, "DB_PATH", db_file)
    forge_app.init_db()
    forge_app.migrate_db()
    return forge_app, db_file


def test_migrate_on_fresh_database_creates_articles_and_archive(tmp_path, monkeypatch):
    import sqlite3

    _, db_file = _fresh_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(db_file))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"articles", "signals_archive", "events_archive",
                "artifacts_archive"} <= tables
        article_cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
        assert "tier" in article_cols
        signal_cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
        assert {"published_at", "publish_slug"} <= signal_cols
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path, monkeypatch):
    forge_app, _ = _fresh_db(tmp_path, monkeypatch)
    forge_app.migrate_db()  # second run must not raise

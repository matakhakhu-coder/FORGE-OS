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


def _client(tmp_path, monkeypatch):
    """A Flask test client backed by a fresh, migrated temp database."""
    import core.db.connection as connection

    forge_app, db_file = _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(connection, "DB_PATH", db_file)
    application = forge_app.create_app()
    application.config["TESTING"] = True
    return application.test_client(), db_file


def _insert_signal(db_file, signal_id, stream, gravity):
    import sqlite3
    from datetime import datetime, timezone

    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(
            "INSERT INTO signals (signal_id, source, external_id, title, content, "
            "timestamp, stream, gravity_score) VALUES (?, 'test', ?, ?, 'body', ?, ?, ?)",
            (signal_id, signal_id, f"title {signal_id}",
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), stream, gravity),
        )
        conn.commit()
    finally:
        conn.close()


def test_document_signals_page_loads(tmp_path, monkeypatch):
    client, db_file = _client(tmp_path, monkeypatch)
    _insert_signal(db_file, "sig-a", "CRIME_INTEL", 0.8)
    resp = client.get("/document/signals?days=7")
    assert resp.status_code == 200
    assert "title sig-a" in resp.get_data(as_text=True)


def test_document_signals_stream_is_not_injectable(tmp_path, monkeypatch):
    from core.web.blueprints.admin import _document_signals_data

    client, db_file = _client(tmp_path, monkeypatch)
    _insert_signal(db_file, "sig-a", "CRIME_INTEL", 0.8)
    _insert_signal(db_file, "sig-b", "GLOBAL", 0.2)

    resp = client.get("/document/signals", query_string={"stream": "x' OR '1'='1"})
    assert resp.status_code == 200

    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        assert _document_signals_data(conn, "x' OR '1'='1", 7)["total"] == 0
        assert _document_signals_data(conn, "CRIME_INTEL", 7)["total"] == 1
        assert _document_signals_data(conn, None, 7)["total"] == 2
    finally:
        conn.close()

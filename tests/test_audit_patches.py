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


def test_event_and_artifact_edit_pages_render(tmp_path, monkeypatch):
    """The edit templates built their delete URL with an endpoint name that
    lacked the 'admin.' blueprint prefix, so both pages raised BuildError."""
    import sqlite3

    client, db_file = _client(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(db_file))
    conn.execute("INSERT INTO events (title, date) VALUES ('ev', '2026-10-01')")
    conn.execute("INSERT INTO artifacts (title, type, date) VALUES ('ar', 'document', '2026-10-01')")
    conn.commit()
    conn.close()

    ev = client.get("/event/1/edit")
    ar = client.get("/artifact/1/edit")
    assert ev.status_code == 200 and "/event/1/delete" in ev.get_data(as_text=True)
    assert ar.status_code == 200 and "/artifact/1/delete" in ar.get_data(as_text=True)


def test_new_event_form_does_not_render_admin_password(tmp_path, monkeypatch):
    import core.web.blueprints.admin as admin_bp_module

    canary = "canary-admin-pw-7f3c"
    monkeypatch.setattr(admin_bp_module, "ADMIN_PASSWORD", canary)
    client, _ = _client(tmp_path, monkeypatch)

    for url in ("/admin/event/new", "/admin"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert canary not in resp.get_data(as_text=True), url

    page = client.get("/admin/event/new").get_data(as_text=True)
    assert 'type="password"' in page and 'name="password"' in page


def test_new_event_post_rejects_wrong_password(tmp_path, monkeypatch):
    import sqlite3
    import core.web.blueprints.admin as admin_bp_module

    monkeypatch.setattr(admin_bp_module, "ADMIN_PASSWORD", "right-pw")
    client, db_file = _client(tmp_path, monkeypatch)

    bad = client.post("/admin/event/new",
                      data={"password": "wrong", "ev_title": "X", "signal_id": "sig-1"})
    assert bad.status_code == 302 and "signal_id=sig-1" in bad.headers["Location"]

    good = client.post("/admin/event/new", data={"password": "right-pw", "ev_title": "X"})
    assert good.status_code == 302

    conn = sqlite3.connect(str(db_file))
    try:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE title='X'").fetchone()[0] == 1
    finally:
        conn.close()


def test_api_health_reports_ok(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "db": "ok"}


def test_paid_pro_feed_is_retired():
    """pro-feed.json used to be written only for --tier pro builds. The paid
    tier was removed on 22 Oct 2026: no build writes it, a build deletes any
    leftover copy, and the open records.json replaces it."""
    import tools.publish as pub

    assert not hasattr(pub, "_writes_pro_feed")
    assert "pro-feed.json" in pub.RETIRED_DIST_FILES


def test_api_archive_lists_archived_cases(tmp_path, monkeypatch):
    """/api/archive selected cases.title, which does not exist (it is 'name')."""
    import sqlite3

    client, db_file = _client(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(db_file))
    conn.execute("INSERT INTO cases (name, status) VALUES ('Old case', 'archived')")
    conn.execute("INSERT INTO cases (name, status) VALUES ('Open case', 'active')")
    conn.commit()
    conn.close()

    resp = client.get("/api/archive")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["archived_cases"][0]["title"] == "Old case"

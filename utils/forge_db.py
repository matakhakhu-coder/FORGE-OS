import sqlite3
import uuid
from datetime import datetime, UTC


def now_utc():
    return datetime.now(UTC).isoformat()


def open_database(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def ensure_actor(cur, name: str, actor_type: str = 'government') -> int:
    cur.execute("SELECT actor_id FROM actors WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row['actor_id']

    cur.execute(
        "INSERT INTO actors (name, type, created_at) VALUES (?, ?, ?)",
        (name, actor_type, now_utc())
    )
    return cur.lastrowid


def ensure_artifact(cur, title: str, description: str, artifact_type: str, source: str) -> int:
    cur.execute("SELECT artifact_id FROM artifacts WHERE title = ?", (title,))
    row = cur.fetchone()
    if row:
        return row['artifact_id']

    cur.execute(
        "INSERT INTO artifacts (title, description, type, source, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, description, artifact_type, source, now_utc())
    )
    return cur.lastrowid


def ensure_signal(
    cur,
    source: str,
    external_id: str,
    title: str,
    content: str,
    status: str = 'raw',
    stream: str = 'GLOBAL',
    source_type: str = 'live',
    artifact_id: int | None = None,
    signal_id: str | None = None
):
    cur.execute("SELECT signal_id FROM signals WHERE external_id = ?", (external_id,))
    row = cur.fetchone()
    if row:
        return row['signal_id'], False

    if signal_id is None:
        signal_id = str(uuid.uuid4())
    columns = [
        'signal_id', 'source', 'external_id', 'title', 'content',
        'status', 'timestamp', 'stream', 'source_type'
    ]
    values = [
        signal_id, source, external_id, title, content,
        status, now_utc(), stream, source_type
    ]

    if artifact_id is not None:
        columns.append('source_artifact_id')
        values.append(artifact_id)

    placeholders = ', '.join(['?'] * len(values))
    cur.execute(
        f"INSERT INTO signals ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(values)
    )
    return signal_id, True


def ensure_actor_signal_link(cur, actor_id: int, signal_id: str) -> bool:
    cur.execute("CREATE TABLE IF NOT EXISTS actor_signals (actor_id INTEGER, signal_id TEXT, PRIMARY KEY(actor_id, signal_id))")
    cur.execute(
        "INSERT OR IGNORE INTO actor_signals (actor_id, signal_id) VALUES (?, ?)",
        (actor_id, signal_id)
    )
    return cur.rowcount > 0


def ensure_actor_artifact_link(cur, actor_id: int, artifact_id: int) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='actor_artifacts'")
    if not cur.fetchone():
        return False

    cur.execute(
        "INSERT OR IGNORE INTO actor_artifacts (actor_id, artifact_id) VALUES (?, ?)",
        (actor_id, artifact_id)
    )
    return cur.rowcount > 0

import sqlite3
import re
from typing import Any, Dict, List, Optional

NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(text: str) -> str:
    if not text:
        return ""
    name = text.strip().lower()
    name = NORMALIZE_RE.sub(" ", name)
    return " ".join(part for part in name.split() if part)


class EntityResolver:
    """Resolve named actors against the FORGE actors table."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _find_actor(self, name: str) -> Optional[int]:
        normalized = normalize_name(name)
        if not normalized:
            return None

        cur = self.conn.cursor()
        cur.execute(
            "SELECT actor_id FROM actors WHERE lower(name) = ? OR lower(name) LIKE ?",
            (name.lower(), f"%{name.lower()}%"),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])

        # fallback near-normalized match
        cur.execute("SELECT actor_id, name FROM actors")
        for actor_id, actor_name in cur.fetchall():
            if normalize_name(actor_name) == normalized:
                return int(actor_id)

        return None

    def _create_actor(self, name: str) -> int:
        normalized = normalize_name(name)
        conn = self.conn
        cur = conn.cursor()

        # Use a valid actor type to satisfy DB CHECK constraints.
        cur.execute("INSERT INTO actors (name, type, created_at) VALUES (?, ?, datetime('now'))", (name.strip(), 'institution'))
        conn.commit()
        return cur.lastrowid

    def resolve_actors(self, actors: List[str]) -> List[Dict[str, Any]]:
        resolved = []
        for actor_name in actors:
            if not actor_name or not actor_name.strip():
                continue

            actor_id = self._find_actor(actor_name)
            if actor_id is None:
                actor_id = self._create_actor(actor_name)
            resolved.append({"actor_id": actor_id, "name": actor_name})

        return resolved

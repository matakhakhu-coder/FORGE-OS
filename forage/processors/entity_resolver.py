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
    """
    Resolve named actors against the FORGE actors table.

    D-1: Index-first resolution — builds a {normalized_name: actor_id} dict
    once on construction so every subsequent lookup is O(1) instead of
    O(n) full-table scan per call.  The index is rebuilt on demand via
    refresh() when new actors have been created mid-session.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._index: Dict[str, int] = {}          # normalized_name → actor_id
        self._exact: Dict[str, int] = {}          # exact lower(trim(name)) → actor_id
        self._build_index()

    # ── Index management ──────────────────────────────────────────────────────

    def _build_index(self) -> None:
        """Load all actors into memory as two fast lookup dicts."""
        self._index = {}
        self._exact = {}
        for row in self.conn.execute(
            "SELECT actor_id, name FROM actors WHERE name IS NOT NULL"
        ):
            actor_id = int(row[0])
            name     = row[1]
            if not name or not name.strip():
                continue
            exact = name.strip().lower()
            norm  = normalize_name(name)
            if exact:
                self._exact[exact] = actor_id
            if norm:
                self._index[norm]  = actor_id

    def refresh(self) -> None:
        """Rebuild index — call after creating new actors mid-session."""
        self._build_index()

    # ── Resolution ────────────────────────────────────────────────────────────

    def _find_actor(self, name: str) -> Optional[int]:
        if not name or not name.strip():
            return None

        # 1. Exact case-insensitive match — O(1)
        exact = name.strip().lower()
        if exact in self._exact:
            return self._exact[exact]

        # 2. Normalized token match — O(1)
        norm = normalize_name(name)
        if norm and norm in self._index:
            return self._index[norm]

        return None

    def _create_actor(self, name: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO actors (name, type, created_at) VALUES (?, ?, datetime('now'))",
            (name.strip(), "institution"),
        )
        self.conn.commit()
        actor_id = cur.lastrowid
        # Update both indexes immediately so subsequent calls in this session
        # don't create duplicates.
        exact = name.strip().lower()
        norm  = normalize_name(name)
        if exact:
            self._exact[exact] = actor_id
        if norm:
            self._index[norm]  = actor_id
        return actor_id

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

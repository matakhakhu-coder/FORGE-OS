from __future__ import annotations
import sqlite3
import re
from typing import Any, Dict, List, Optional

NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

# Fuzzy matching threshold — only applied when exact + token matches fail.
# 0.92 is tight enough to unify "Cyril Ramaphosa" / "C Ramaphosa" / "Cyril M Ramaphosa"
# while rejecting "Ramaphosa" vs "Ramaphosa Foundation" (different entities).
FUZZY_THRESHOLD = 0.92

# Minimum name length to attempt fuzzy matching (skip initials and short acronyms)
FUZZY_MIN_LENGTH = 5


def normalize_name(text: str) -> str:
    if not text:
        return ""
    name = text.strip().lower()
    name = NORMALIZE_RE.sub(" ", name)
    return " ".join(part for part in name.split() if part)


def _jaro_similarity(s1: str, s2: str) -> float:
    """Jaro similarity between two strings. Returns 0.0–1.0."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2

    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (matches / len1 + matches / len2 +
            (matches - transpositions / 2) / matches) / 3
    return jaro


def _jaro_winkler(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler similarity. Boosts score for common prefixes."""
    jaro = _jaro_similarity(s1, s2)

    # Common prefix (up to 4 chars)
    prefix_len = 0
    for i in range(min(len(s1), len(s2), 4)):
        if s1[i] == s2[i]:
            prefix_len += 1
        else:
            break

    return jaro + prefix_len * prefix_weight * (1.0 - jaro)


class EntityResolver:
    """
    Resolve named actors against the FORGE actors table.

    Three-tier resolution strategy (Stable 1.2.1):
      1. Exact case-insensitive match — O(1) dict lookup
      2. Normalized token match — O(1) dict lookup
      3. Fuzzy Jaro-Winkler match — O(k) where k = candidates sharing
         a first-token prefix (typically 1-10 actors, not the full table)

    The fuzzy tier activates only when tiers 1+2 miss, and only for names
    longer than FUZZY_MIN_LENGTH chars. It uses a token-prefix index to
    reduce the candidate set before computing Jaro-Winkler similarity.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._index: Dict[str, int] = {}
        self._exact: Dict[str, int] = {}
        self._prefix_index: Dict[str, list[tuple[str, int]]] = {}
        self._all_names: Dict[int, str] = {}
        self._build_index()

    def _build_index(self) -> None:
        self._index = {}
        self._exact = {}
        self._prefix_index = {}
        self._all_names = {}
        for row in self.conn.execute(
            "SELECT actor_id, name FROM actors WHERE name IS NOT NULL"
        ):
            actor_id = int(row[0])
            name     = row[1]
            if not name or not name.strip():
                continue
            self._register(actor_id, name)

    def _register(self, actor_id: int, name: str) -> None:
        exact = name.strip().lower()
        norm  = normalize_name(name)
        if exact:
            self._exact[exact] = actor_id
        if norm:
            self._index[norm] = actor_id
            self._all_names[actor_id] = norm
            # Token index: every token (len >= 3) → [(norm_name, actor_id), ...]
            # Indexes all tokens so "Cyril Ramaphosa" is findable via
            # either "cyril" or "ramaphosa" as a query token.
            for token in norm.split():
                if len(token) >= 3:
                    if token not in self._prefix_index:
                        self._prefix_index[token] = []
                    self._prefix_index[token].append((norm, actor_id))

    def refresh(self) -> None:
        self._build_index()

    def _find_actor(self, name: str) -> Optional[int]:
        if not name or not name.strip():
            return None

        # Tier 1: Exact case-insensitive — O(1)
        exact = name.strip().lower()
        if exact in self._exact:
            return self._exact[exact]

        # Tier 2: Normalized token — O(1)
        norm = normalize_name(name)
        if norm and norm in self._index:
            return self._index[norm]

        # Tier 3: Fuzzy Jaro-Winkler — O(k) with prefix reduction
        if norm and len(norm) >= FUZZY_MIN_LENGTH:
            match = self._fuzzy_match(norm)
            if match is not None:
                return match

        return None

    def _fuzzy_match(self, query_norm: str) -> Optional[int]:
        """
        Attempt fuzzy match using Jaro-Winkler similarity.
        Uses the token-prefix index to reduce candidates: only actors
        whose first token matches the query's first token (or any token
        in the query) are compared. This keeps the search at O(k) where
        k is typically 1-10, not 1,000+.
        """
        query_tokens = query_norm.split()
        if not query_tokens:
            return None

        # Gather candidates from prefix index
        candidates: set[int] = set()
        for token in query_tokens:
            if token in self._prefix_index:
                for _, aid in self._prefix_index[token]:
                    candidates.add(aid)

        # Also check the last token (surname matching: "C Ramaphosa" → "Ramaphosa")
        last_token = query_tokens[-1] if len(query_tokens) > 1 else ""
        if last_token and last_token in self._prefix_index:
            for _, aid in self._prefix_index[last_token]:
                candidates.add(aid)

        if not candidates:
            return None

        best_score = 0.0
        best_id: Optional[int] = None

        for actor_id in candidates:
            existing_norm = self._all_names.get(actor_id, "")
            if not existing_norm:
                continue

            score = _jaro_winkler(query_norm, existing_norm)

            # Also try matching just the surname (last token) against
            # existing full name — handles "C Ramaphosa" vs "Cyril Ramaphosa"
            if len(query_tokens) >= 2:
                existing_tokens = existing_norm.split()
                if existing_tokens:
                    surname_score = _jaro_winkler(query_tokens[-1], existing_tokens[-1])
                    # If surnames are identical and query has an initial that matches
                    if surname_score >= 0.98 and len(query_tokens[0]) <= 2:
                        if existing_tokens[0].startswith(query_tokens[0][0]):
                            score = max(score, 0.95)

            if score > best_score:
                best_score = score
                best_id = actor_id

        if best_score >= FUZZY_THRESHOLD and best_id is not None:
            return best_id

        return None

    def _create_actor(self, name: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO actors (name, type, created_at) VALUES (?, ?, datetime('now'))",
            (name.strip(), "institution"),
        )
        self.conn.commit()
        actor_id = cur.lastrowid
        self._register(actor_id, name.strip())
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

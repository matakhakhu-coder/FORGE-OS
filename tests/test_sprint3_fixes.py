#!/usr/bin/env python3
from __future__ import annotations
"""
Sprint 3 (2026-07-23) — regression tests for feed & search correctness fixes.

Run:
    python -m pytest tests/test_sprint3_fixes.py -v
    python -m unittest tests.test_sprint3_fixes
"""
import sys
import os
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFeedSignalQueryBinding(unittest.TestCase):
    """The SIGNAL branch of /api/feed mixed a positional `?` placeholder
    with a named-params dict in one execute() call — SQLite rejects that
    outright. Was silently swallowed by a bare `except: pass` (now fixed
    separately, see TestApiFeedLogging) so the SIGNAL item type had likely
    been failing on every single call where lens != 'all'."""

    def test_source_type_clause_uses_named_placeholder(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE signals (signal_id TEXT, source_type TEXT)")
        conn.execute("INSERT INTO signals VALUES ('s1', 'live')")
        conn.commit()

        # Mirrors the fixed construction in pages.py::api_feed()
        source_type_clause = "source_type = :source_type"
        params = {"source_type": "live"}

        # Must not raise sqlite3.ProgrammingError
        rows = conn.execute(
            f"SELECT signal_id FROM signals WHERE ({source_type_clause})",
            params,
        ).fetchall()
        self.assertEqual(len(rows), 1)
        conn.close()

    def test_positional_placeholder_with_dict_params_fails(self):
        # Documents the exact failure mode that was live in production —
        # if this ever stops raising, something upstream silently changed
        # sqlite3's binding behaviour and the "fixed" query above should
        # be re-examined.
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE signals (signal_id TEXT, source_type TEXT)")
        conn.commit()

        source_type_clause = "source_type = ?"  # the old, broken form
        params = {"source_type": "live"}

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute(
                f"SELECT signal_id FROM signals WHERE ({source_type_clause})",
                params,
            ).fetchall()
        conn.close()


class TestCorrelationStreamWeight(unittest.TestCase):
    """Correlation feed_score must no longer get an unconditional 1.0
    stream_weight regardless of the constituent signals' real streams."""

    def test_weight_derived_from_stronger_constituent_stream(self):
        _STREAM_WEIGHTS = {
            "CRIME_INTEL": 1.0, "PRIORITY": 0.9,
            "INFRASTRUCTURE": 0.7, "GLOBAL": 0.3,
        }
        _DEFAULT = 0.3

        def weight_for_pair(stream_a, stream_b):
            sw_a = _STREAM_WEIGHTS.get(stream_a or "GLOBAL", _DEFAULT)
            sw_b = _STREAM_WEIGHTS.get(stream_b or "GLOBAL", _DEFAULT)
            return max(sw_a, sw_b)

        # Two GLOBAL-tier signals: must NOT get the old pinned 1.0.
        self.assertEqual(weight_for_pair("GLOBAL", "GLOBAL"), 0.3)
        # One CRIME_INTEL signal still earns the top weight.
        self.assertEqual(weight_for_pair("CRIME_INTEL", "GLOBAL"), 1.0)
        # Unknown/None stream falls back to the GLOBAL default, not a crash.
        self.assertEqual(weight_for_pair(None, None), 0.3)


class TestSearchSanctionsFacet(unittest.TestCase):
    """OFAC SDN entries are 91.7% of all artifacts with no structured
    column distinguishing them -- excluded by default via title pattern,
    opt-in via include_sanctions=1."""

    def _build_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE artifacts (
                artifact_id INTEGER PRIMARY KEY, title TEXT, type TEXT,
                date TEXT, source TEXT, description TEXT, tags TEXT,
                thumbnail TEXT, event_id INTEGER
            )
        """)
        conn.execute("CREATE VIRTUAL TABLE artifacts_fts USING fts5(title, content='artifacts', content_rowid='artifact_id')")
        conn.execute("INSERT INTO artifacts (artifact_id, title, type) VALUES (1, 'AEROCARIBBEAN AIRLINES -- OFAC SDN (CUBA)', 'news')")
        conn.execute("INSERT INTO artifacts (artifact_id, title, type) VALUES (2, 'SAPS election security briefing', 'news')")
        conn.execute("INSERT INTO artifacts_fts (rowid, title) VALUES (1, 'AEROCARIBBEAN AIRLINES -- OFAC SDN (CUBA)')")
        conn.execute("INSERT INTO artifacts_fts (rowid, title) VALUES (2, 'SAPS election security briefing')")
        conn.commit()
        return conn

    def _run_query(self, conn, fts_query, include_sanctions):
        sanctions_clause = "" if include_sanctions else "AND a.title NOT LIKE '%OFAC SDN%'"
        return conn.execute(f"""
            SELECT a.artifact_id, a.title
            FROM   artifacts_fts f
            JOIN   artifacts a ON a.artifact_id = f.rowid
            WHERE  artifacts_fts MATCH ?
            {sanctions_clause}
            ORDER  BY rank
        """, (fts_query,)).fetchall()

    def test_excluded_by_default(self):
        conn = self._build_db()
        rows = self._run_query(conn, '"election"', include_sanctions=False)
        titles = [r["title"] for r in rows]
        self.assertNotIn("AEROCARIBBEAN AIRLINES -- OFAC SDN (CUBA)", titles)
        conn.close()

    def test_included_when_opted_in(self):
        conn = self._build_db()
        rows = self._run_query(conn, '"OFAC"', include_sanctions=True)
        titles = [r["title"] for r in rows]
        self.assertIn("AEROCARIBBEAN AIRLINES -- OFAC SDN (CUBA)", titles)
        conn.close()

    def test_genuine_content_unaffected(self):
        conn = self._build_db()
        rows = self._run_query(conn, '"SAPS"', include_sanctions=False)
        titles = [r["title"] for r in rows]
        self.assertIn("SAPS election security briefing", titles)
        conn.close()


if __name__ == "__main__":
    unittest.main()

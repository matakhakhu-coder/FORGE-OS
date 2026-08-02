#!/usr/bin/env python3
from __future__ import annotations
"""
Sprint 1 (2026-07-23) — regression tests for the launch-readiness data
integrity fixes: fake collector coordinates, actor-type defaulting, the
is_targeted MAX-without-normalization scoring bug, and the case
description/hypothesis field swap.

None of these touch the live database.db — each builds its own isolated
in-memory sqlite connection or asserts against pure in-repo data structures.

Run:
    python -m pytest tests/test_sprint1_fixes.py -v
    python -m unittest tests.test_sprint1_fixes
"""
import sys
import os
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forage.collectors.civic_intel_collector import SOURCES
from forage.processors.signal_interpreter import SignalInterpreter, _extract_actor_types
from forage.processors.entity_resolver import EntityResolver
from core.conclave.registry import AnalysisResult
from core.conclave.engine import run_conclave
from forage.engines.entity_engine import materialize_entities


class TestNoDefaultCoordinates(unittest.TestCase):
    """Blocker #4: sources must not stamp a shared fake coordinate onto
    every article — that produced mathematically-exact 0km-apart false
    correlations. See forage/collectors/civic_intel_collector.py."""

    def test_no_source_has_default_lat_lng(self):
        offenders = [
            s["source_key"] for s in SOURCES
            if "default_lat" in s or "default_lng" in s
        ]
        self.assertEqual(
            offenders, [],
            f"sources still carry a hardcoded default coordinate: {offenders}",
        )

    def test_all_fourteen_sources_still_present(self):
        # Guard against the coordinate-removal edit accidentally dropping
        # a whole source entry.
        self.assertEqual(len(SOURCES), 14)


class TestActorTypeThreading(unittest.TestCase):
    """Blocker #6: place names must not default to 'institution'."""

    def test_extract_actor_types_types_location_correctly(self):
        types = _extract_actor_types("Officials in Cape Town and Pretoria met today.")
        self.assertEqual(types.get("Cape Town"), "location")
        self.assertEqual(types.get("Pretoria"), "location")

    def test_extract_actor_types_types_npa_as_institution(self):
        types = _extract_actor_types("The National Prosecuting Authority opened a case.")
        self.assertEqual(types.get("National Prosecuting Authority"), "institution")

    def test_interpret_exposes_actor_types_alongside_actors(self):
        interpreted = SignalInterpreter().interpret({
            "title": "Cape Town municipality flags NPA probe",
            "content": "The NPA is investigating.",
        })
        self.assertIn("Cape Town", interpreted["actors"])
        self.assertEqual(interpreted["actor_types"].get("Cape Town"), "location")

    def test_entity_resolver_creates_actor_with_passed_type(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE actors (actor_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT, type TEXT, created_at TEXT)"
        )
        resolver = EntityResolver(conn)
        resolver.resolve_actors(["Cape Town"], {"Cape Town": "location"})
        row = conn.execute(
            "SELECT type FROM actors WHERE name='Cape Town'"
        ).fetchone()
        self.assertEqual(row["type"], "location")
        conn.close()

    def test_entity_resolver_defaults_to_institution_without_type_map(self):
        # Backward-compat: no actor_types passed -> unchanged old behaviour.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE actors (actor_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT, type TEXT, created_at TEXT)"
        )
        resolver = EntityResolver(conn)
        resolver.resolve_actors(["Some New Org"])
        row = conn.execute(
            "SELECT type FROM actors WHERE name='Some New Org'"
        ).fetchone()
        self.assertEqual(row["type"], "institution")
        conn.close()

    def test_entity_resolver_blocks_category_label_names(self):
        # Follow-up fix (2026-07-23): a stray actor literally named
        # "location" was found live in the DB — entity_engine.py guards
        # against category-label names, entity_resolver.py's own creation
        # path didn't. Must not create an actor for a blocked name.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE actors (actor_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT, type TEXT, created_at TEXT)"
        )
        resolver = EntityResolver(conn)
        resolved = resolver.resolve_actors(["location", "government", "Cape Town"])
        names_created = {r["name"] for r in resolved}
        self.assertNotIn("location", names_created)
        self.assertNotIn("government", names_created)
        self.assertIn("Cape Town", names_created)
        row = conn.execute("SELECT COUNT(*) AS n FROM actors WHERE name='location'").fetchone()
        self.assertEqual(row["n"], 0)
        conn.close()

    def test_run_conclave_merges_entity_types_to_top_level(self):
        # This was the deepest part of the bug: even a result whose OWN
        # provenance correctly typed an entity lost that typing during
        # merge, because provenance became {"sources": [...]} with no
        # flattened entity_types at the top level.
        result_a = AnalysisResult(
            entities=["Cape Town"], intent="unknown", gravity=0.1,
            recommendation="IGNORE", confidence=0.5,
            provenance={"entity_types": {"Cape Town": "location"}},
        )
        result_b = AnalysisResult(
            entities=["NPA"], intent="unknown", gravity=0.1,
            recommendation="IGNORE", confidence=0.5,
            provenance={"entity_types": {"NPA": "institution"}},
        )
        conclusion = run_conclave([result_a, result_b])
        self.assertEqual(
            conclusion.provenance["entity_types"],
            {"Cape Town": "location", "NPA": "institution"},
        )
        # Original "sources" list must still be present — nothing else
        # reading it should break.
        self.assertEqual(len(conclusion.provenance["sources"]), 2)

    def test_materialize_entities_uses_merged_entity_types(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE actors (actor_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT, type TEXT, created_at TEXT, confidence_score REAL, "
            "automated INTEGER, source_type TEXT DEFAULT 'live', "
            "blacklisted INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE signals (signal_id TEXT PRIMARY KEY, "
            "conclave_meta TEXT, gravity_score REAL)"
        )
        conn.execute("INSERT INTO signals (signal_id) VALUES ('sig1')")
        conn.commit()

        conclusion = AnalysisResult(
            entities=["Cape Town", "National Prosecuting Authority"],
            intent="unknown", gravity=0.1, recommendation="IGNORE", confidence=0.5,
            provenance={"entity_types": {
                "Cape Town": "location",
                "National Prosecuting Authority": "institution",
            }},
        )
        materialize_entities(conclusion, "sig1", conn)
        rows = {r["name"]: r["type"] for r in conn.execute("SELECT name, type FROM actors")}
        self.assertEqual(rows.get("Cape Town"), "location")
        self.assertEqual(rows.get("National Prosecuting Authority"), "institution")
        conn.close()


class TestIsTargetedNormalization(unittest.TestCase):
    """Blocker #3: is_targeted must not trip on volume alone. A location
    is never eligible; everything else needs a hot-signal ratio at least
    2x the corpus baseline, not just one hit out of thousands."""

    def _hot_ratio_query(self, conn):
        return conn.execute("""
            WITH corpus_stats AS (
                SELECT CAST(SUM(CASE WHEN COALESCE(gravity_score,0) >= 0.55
                                           OR COALESCE(is_priority,0) = 1
                                      THEN 1 ELSE 0 END) AS REAL)
                       / NULLIF(COUNT(*), 0) AS base_rate
                FROM   signals
            ),
            actor_signal_stats AS (
                SELECT sa.actor_id,
                       COUNT(DISTINCT sa.signal_id) AS signal_count,
                       COUNT(DISTINCT CASE
                                 WHEN COALESCE(s.gravity_score, 0) >= 0.55
                                      OR COALESCE(s.is_priority, 0) = 1
                                 THEN sa.signal_id END) AS hot_signal_count
                FROM   signal_actors sa
                JOIN   signals s ON s.signal_id = sa.signal_id
                GROUP  BY sa.actor_id
            )
            SELECT ac.actor_id, ac.name, ac.type,
                   CASE WHEN ac.type != 'location'
                             AND COALESCE(ass.signal_count, 0) > 0
                             AND (CAST(COALESCE(ass.hot_signal_count, 0) AS REAL)
                                  / ass.signal_count)
                                 >= 2.0 * (SELECT base_rate FROM corpus_stats)
                        THEN 1 ELSE 0 END AS is_targeted
            FROM   actors ac
            LEFT   JOIN actor_signal_stats ass ON ass.actor_id = ac.actor_id
        """).fetchall()

    def _build_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE actors (actor_id INTEGER PRIMARY KEY, name TEXT, type TEXT)"
        )
        conn.execute(
            "CREATE TABLE signals (signal_id TEXT PRIMARY KEY, gravity_score REAL, is_priority INTEGER)"
        )
        conn.execute(
            "CREATE TABLE signal_actors (actor_id INTEGER, signal_id TEXT)"
        )
        return conn

    def test_high_volume_low_gravity_actor_is_not_targeted(self):
        # A place-name-shaped actor linked to 1000 mostly-cold signals,
        # with a hot-rate matching the corpus baseline (~10%), must NOT
        # be flagged — this is exactly the "Gauteng" failure mode: high
        # volume, unremarkable ratio.
        conn = self._build_db()
        conn.execute("INSERT INTO actors VALUES (1, 'Generic Place', 'institution')")
        for i in range(1000):
            hot = 1 if i < 100 else 0  # 10% hot, matches corpus baseline below
            conn.execute(
                "INSERT INTO signals VALUES (?, ?, ?)",
                (f"s{i}", 0.9 if hot else 0.1, 0),
            )
            conn.execute("INSERT INTO signal_actors VALUES (1, ?)", (f"s{i}",))
        conn.commit()
        rows = self._hot_ratio_query(conn)
        self.assertEqual(rows[0]["is_targeted"], 0)
        conn.close()

    def test_low_volume_high_gravity_actor_is_targeted(self):
        # A rare actor whose few linked signals are almost all hot should
        # still be flagged — the fix must not punish legitimately small,
        # highly-relevant actors. Needs background signals unlinked to any
        # actor so the corpus baseline reflects the wider corpus, not just
        # this one actor's own signals (otherwise base_rate == the actor's
        # own rate and the 2x bar becomes mathematically unreachable).
        conn = self._build_db()
        conn.execute("INSERT INTO actors VALUES (1, 'Cat Matlala', 'person')")
        for i in range(500):
            hot = 1 if i < 75 else 0  # 15% background hot-rate
            conn.execute(
                "INSERT INTO signals VALUES (?, ?, ?)",
                (f"bg{i}", 0.9 if hot else 0.1, 0),
            )
        for i in range(15):
            hot = 1 if i < 14 else 0  # this actor: 93% hot
            conn.execute(
                "INSERT INTO signals VALUES (?, ?, ?)",
                (f"s{i}", 0.9 if hot else 0.1, 0),
            )
            conn.execute("INSERT INTO signal_actors VALUES (1, ?)", (f"s{i}",))
        conn.commit()
        rows = self._hot_ratio_query(conn)
        rows = [r for r in rows if r["name"] == "Cat Matlala"]
        self.assertEqual(rows[0]["is_targeted"], 1)
        conn.close()

    def test_location_type_is_never_targeted_even_with_high_ratio(self):
        # Even an artificially 100%-hot actor must not be flagged if its
        # type is 'location' — a city cannot be an investigative target.
        conn = self._build_db()
        conn.execute("INSERT INTO actors VALUES (1, 'Pretoria', 'location')")
        for i in range(50):
            conn.execute("INSERT INTO signals VALUES (?, 0.9, 0)", (f"s{i}",))
            conn.execute("INSERT INTO signal_actors VALUES (1, ?)", (f"s{i}",))
        conn.commit()
        rows = self._hot_ratio_query(conn)
        self.assertEqual(rows[0]["is_targeted"], 0)
        conn.close()


class TestCaseFieldSwap(unittest.TestCase):
    """Blocker #2: the case list displays `description` — it must get the
    readable narrative, not the raw signal-ID dump. Tests the route
    function directly against an isolated in-memory DB."""

    def _build_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE signals (
                signal_id TEXT PRIMARY KEY, title TEXT, source TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE correlated_incidents (
                id INTEGER PRIMARY KEY, signal_a TEXT, signal_b TEXT,
                correlation_score REAL, distance_km REAL, time_difference_hours REAL
            )
        """)
        conn.execute("""
            CREATE TABLE cases (
                case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, description TEXT, hypothesis TEXT,
                status TEXT, case_type TEXT
            )
        """)
        conn.execute("INSERT INTO signals VALUES ('abcd1234efgh', 'Article A', 'groundup')")
        conn.execute("INSERT INTO signals VALUES ('ijkl5678mnop', 'Article B', 'timeslive_corruption')")
        conn.execute(
            "INSERT INTO correlated_incidents VALUES (675, 'abcd1234efgh', 'ijkl5678mnop', 0.989, 0.0, 0.1)"
        )
        conn.commit()
        return conn

    def test_description_gets_readable_text_hypothesis_gets_raw_ids(self):
        from flask import Flask
        import core.web.blueprints.cases as cases_module

        conn = self._build_db()
        app = Flask(__name__)

        original_get_db = cases_module.get_db
        cases_module.get_db = lambda: conn
        try:
            with app.test_request_context(
                "/api/correlations/promote-case",
                method="POST",
                json={"correlation_id": 675},
            ):
                resp = cases_module.api_correlation_promote_case()
        finally:
            cases_module.get_db = original_get_db

        row = conn.execute(
            "SELECT description, hypothesis FROM cases WHERE case_id = 1"
        ).fetchone()
        conn.close()

        # description (shown on the case list) must be the readable summary
        self.assertIn("km apart", row["description"])
        self.assertIn("Correlation score", row["description"])
        self.assertNotIn("#675", row["description"])

        # hypothesis (detail-panel only) carries the raw provenance instead
        self.assertIn("#675", row["hypothesis"])
        self.assertIn("abcd1234", row["hypothesis"])


if __name__ == "__main__":
    unittest.main()

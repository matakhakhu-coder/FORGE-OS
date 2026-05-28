#!/usr/bin/env python3
from __future__ import annotations
"""
CT-1 Contextual Tunneling — verified test suite.

Covers all public functions in core/gravity.py without requiring a live DB.
build_context() is tested against a lightweight mock connection.

Run:
    python -m pytest tests/test_gravity_ct1.py -v
    python -m unittest tests/test_gravity_ct1.py
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gravity import (
    RADIUS_KM,
    _extract_keywords,
    _haversine_km,
    _location_match,
    extract_location_anchors,
    build_context,
    score_item,
    blend_score,
)

# ── SA reference coordinates ──────────────────────────────────────────────────
_JHB  = (-26.2041, 28.0473)   # Johannesburg
_PTA  = (-25.7461, 28.1881)   # Pretoria — ~58 km from JHB
_CPT  = (-33.9249, 18.4241)   # Cape Town — ~1270 km from JHB
_DBN  = (-29.8587, 30.9798)   # Durban    — ~580 km from JHB


# ── Mock DB helpers ───────────────────────────────────────────────────────────

class _Row(dict):
    """Minimal sqlite3.Row-alike that supports both dict and attribute access."""
    def __getitem__(self, key):
        return super().__getitem__(key)


def _make_mock_db(case: dict, actor_ids: list, signal_rows: list, case_signal_rows: list):
    """
    Returns a minimal mock DB object whose .execute() handles the four queries
    that build_context() issues.
    """
    class _Cursor:
        def __init__(self, rows):
            self._rows = rows
        def fetchall(self):
            return self._rows
        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _MockDB:
        def execute(self, sql, params=()):
            # Normalise whitespace so multi-space SQL formatting doesn't break matching
            sql_n = " ".join(sql.lower().split())
            if "from case_actors" in sql_n:
                return _Cursor([_Row({"actor_id": aid}) for aid in actor_ids])
            if "from signal_actors" in sql_n:
                return _Cursor([_Row({"signal_id": s}) for s in signal_rows])
            if "from case_signals" in sql_n and "s.lat" in sql_n:
                return _Cursor(case_signal_rows)
            if "from cases" in sql_n:
                return _Cursor([_Row(case)] if case else [])
            if "from case_signals" in sql_n:
                return _Cursor([])
            return _Cursor([])

    return _MockDB()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHaversine(unittest.TestCase):

    def test_same_point_zero(self):
        self.assertAlmostEqual(_haversine_km(*_JHB, *_JHB), 0.0, places=2)

    def test_jhb_to_pta_approx_58km(self):
        d = _haversine_km(*_JHB, *_PTA)
        self.assertGreater(d, 50)
        self.assertLess(d, 70)

    def test_jhb_to_cpt_approx_1270km(self):
        d = _haversine_km(*_JHB, *_CPT)
        self.assertGreater(d, 1200)
        self.assertLess(d, 1350)

    def test_symmetry(self):
        d1 = _haversine_km(*_JHB, *_DBN)
        d2 = _haversine_km(*_DBN, *_JHB)
        self.assertAlmostEqual(d1, d2, places=6)


class TestExtractKeywords(unittest.TestCase):

    def test_stopwords_excluded(self):
        kw = _extract_keywords("the report investigation signal")
        self.assertNotIn("the", kw)
        self.assertNotIn("report", kw)

    def test_short_words_excluded(self):
        kw = _extract_keywords("NPA HAWKS SIU corruption")
        self.assertNotIn("npa", kw)
        self.assertIn("hawks", kw)
        self.assertIn("corruption", kw)

    def test_empty_returns_empty(self):
        self.assertEqual(_extract_keywords(""), set())
        self.assertEqual(_extract_keywords(None), set())

    def test_overlap_detection(self):
        kw_a = _extract_keywords("tender fraud procurement corruption")
        kw_b = _extract_keywords("corruption tender collusion")
        self.assertTrue(kw_a & kw_b)


class TestLocationMatch(unittest.TestCase):

    def test_within_radius_returns_one(self):
        anchors = [_JHB]
        result = _location_match(*_PTA, anchors)
        self.assertEqual(result, 1.0)

    def test_outside_radius_returns_zero(self):
        anchors = [_JHB]
        result = _location_match(*_CPT, anchors)
        self.assertEqual(result, 0.0)

    def test_none_coordinates_returns_zero(self):
        anchors = [_JHB]
        self.assertEqual(_location_match(None, None, anchors), 0.0)
        self.assertEqual(_location_match(None, 28.0, anchors), 0.0)

    def test_empty_anchors_returns_zero(self):
        self.assertEqual(_location_match(*_JHB, []), 0.0)

    def test_multiple_anchors_any_match(self):
        anchors = [_CPT, _JHB]
        self.assertEqual(_location_match(*_PTA, anchors), 1.0)


class TestExtractLocationAnchors(unittest.TestCase):

    def test_known_location_detected(self):
        results = extract_location_anchors("Outbreak reported in Kenya near Nairobi")
        labels = [r["label"] for r in results]
        self.assertIn("kenya", labels)

    def test_sa_not_in_gazetteer(self):
        results = extract_location_anchors("Johannesburg South Africa crime")
        labels = [r["label"] for r in results]
        self.assertNotIn("johannesburg", labels)

    def test_empty_text_returns_empty(self):
        self.assertEqual(extract_location_anchors(""), [])
        self.assertEqual(extract_location_anchors(None), [])

    def test_result_has_lat_lng_label(self):
        results = extract_location_anchors("drc outbreak confirmed")
        self.assertTrue(len(results) > 0)
        for r in results:
            self.assertIn("lat", r)
            self.assertIn("lng", r)
            self.assertIn("label", r)

    def test_deduplication(self):
        results = extract_location_anchors("drc democratic republic drc")
        labels = [r["label"] for r in results]
        self.assertEqual(len(labels), len(set(labels)))


class TestScoreItem(unittest.TestCase):

    def _ctx(self, actor_ids=None, signal_ids=None, locations=None, keywords=None):
        return {
            "case_id":           1,
            "actor_ids":         set(actor_ids or []),
            "signal_ids_linked": set(signal_ids or []),
            "locations":         list(locations or []),
            "keywords":          set(keywords or []),
        }

    def test_empty_context_returns_zero(self):
        ctx = self._ctx()
        self.assertEqual(score_item({"item_type": "SIGNAL"}, ctx), 0.0)

    def test_none_context_returns_zero(self):
        self.assertEqual(score_item({"item_type": "SIGNAL"}, None), 0.0)
        self.assertEqual(score_item({"item_type": "SIGNAL"}, {}), 0.0)

    def test_signal_actor_match_gives_0_5(self):
        # actor_ids must be non-empty to pass the short-circuit guard;
        # signal_ids_linked is derived from actor_ids in build_context.
        ctx = self._ctx(actor_ids=[1], signal_ids=["sig-001"])
        item = {"item_type": "SIGNAL", "signal_id": "sig-001", "lat": None, "lng": None}
        self.assertAlmostEqual(score_item(item, ctx), 0.5, places=4)

    def test_signal_location_match_gives_0_3(self):
        ctx = self._ctx(locations=[_JHB])
        item = {"item_type": "SIGNAL", "lat": _PTA[0], "lng": _PTA[1]}
        self.assertAlmostEqual(score_item(item, ctx), 0.3, places=4)

    def test_signal_keyword_match_full_gives_up_to_0_2(self):
        ctx = self._ctx(keywords={"tender", "corruption", "fraud", "procurement"})
        item = {
            "item_type": "SIGNAL",
            "title": "tender fraud corruption investigation",
            "summary": "procurement irregularities detected",
        }
        score = score_item(item, ctx)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 0.2)

    def test_signal_all_components_max_1(self):
        ctx = self._ctx(
            signal_ids=["sig-X"],
            locations=[_JHB],
            keywords={"tender", "corruption"},
        )
        item = {
            "item_type": "SIGNAL",
            "signal_id": "sig-X",
            "lat": _PTA[0],
            "lng": _PTA[1],
            "title": "tender corruption fraud",
            "summary": "",
        }
        score = score_item(item, ctx)
        self.assertGreater(score, 0.7)
        self.assertLessEqual(score, 1.0)

    def test_sentinel_alert_location_scored(self):
        ctx = self._ctx(locations=[_JHB])
        item = {
            "item_type": "SENTINEL_ALERT",
            "location_lat": _PTA[0],
            "location_lon": _PTA[1],
            "title": "Alert",
            "summary": "",
        }
        self.assertAlmostEqual(score_item(item, ctx), 0.3, places=4)

    def test_intelligence_lead_actor_match(self):
        ctx = self._ctx(actor_ids=[42])
        item = {"item_type": "INTELLIGENCE_LEAD", "actor_id": 42, "actor_name": "unknown"}
        self.assertAlmostEqual(score_item(item, ctx), 0.5, places=4)

    def test_correlation_keyword_only(self):
        ctx = self._ctx(keywords={"tender", "corruption"})
        item = {
            "item_type": "CORRELATION",
            "title_a": "tender fraud case",
            "title_b": "corruption probe launched",
            "title": "",
        }
        score = score_item(item, ctx)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 0.2)

    def test_unknown_item_type_returns_zero(self):
        ctx = self._ctx(keywords={"tender"})
        self.assertEqual(score_item({"item_type": "WIKI_ARTICLE", "title": "tender"}, ctx), 0.0)

    def test_score_bounded_zero_to_one(self):
        ctx = self._ctx(
            signal_ids=["s1"], locations=[_JHB, _CPT], keywords={"fraud", "tender", "corruption"}
        )
        for _ in range(10):
            s = score_item({
                "item_type": "SIGNAL", "signal_id": "s1",
                "lat": -26.2, "lng": 28.0,
                "title": "fraud tender corruption procurement bribery embezzlement",
                "summary": "official corruption tender irregularities",
            }, ctx)
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)


class TestBlendScore(unittest.TestCase):

    def test_weight_zero_is_pure_feed(self):
        self.assertAlmostEqual(blend_score(0.8, 0.2, 0.0), 0.8, places=4)

    def test_weight_one_is_pure_gravity(self):
        self.assertAlmostEqual(blend_score(0.8, 0.2, 1.0), 0.2, places=4)

    def test_weight_half_is_average(self):
        self.assertAlmostEqual(blend_score(0.6, 0.4, 0.5), 0.5, places=4)

    def test_weight_clamped_below_zero(self):
        self.assertAlmostEqual(blend_score(0.8, 0.2, -1.0), 0.8, places=4)

    def test_weight_clamped_above_one(self):
        self.assertAlmostEqual(blend_score(0.8, 0.2, 2.0), 0.2, places=4)


class TestBuildContext(unittest.TestCase):

    def _case(self, name="HAWKS tender fraud", desc="procurement", hypo="collusion"):
        return {
            "name": name, "description": desc,
            "hypothesis": hypo, "context_anchors": None,
        }

    def test_returns_expected_keys(self):
        db = _make_mock_db(self._case(), [1, 2], ["sig-001"], [])
        ctx = build_context(db, 1)
        self.assertIn("case_id", ctx)
        self.assertIn("actor_ids", ctx)
        self.assertIn("signal_ids_linked", ctx)
        self.assertIn("locations", ctx)
        self.assertIn("keywords", ctx)

    def test_actor_ids_populated(self):
        db = _make_mock_db(self._case(), [10, 20], [], [])
        ctx = build_context(db, 1)
        self.assertEqual(ctx["actor_ids"], {10, 20})

    def test_signal_ids_populated(self):
        db = _make_mock_db(self._case(), [1], ["s-abc", "s-xyz"], [])
        ctx = build_context(db, 1)
        self.assertIn("s-abc", ctx["signal_ids_linked"])

    def test_keywords_extracted_from_case(self):
        db = _make_mock_db(self._case("HAWKS tender fraud investigation", "procurement corruption"), [], [], [])
        ctx = build_context(db, 1)
        self.assertIn("hawks", ctx["keywords"])
        self.assertIn("tender", ctx["keywords"])
        self.assertIn("procurement", ctx["keywords"])

    def test_context_anchors_seed_locations(self):
        import json
        anchors = json.dumps([{"lat": -26.2041, "lng": 28.0473}])
        case = {
            "name": "test", "description": "", "hypothesis": "",
            "context_anchors": anchors,
        }
        db = _make_mock_db(case, [], [], [])
        ctx = build_context(db, 1)
        self.assertEqual(len(ctx["locations"]), 1)
        self.assertAlmostEqual(ctx["locations"][0][0], -26.2041, places=3)

    def test_location_anchors_from_pinned_signals(self):
        case = {"name": "test", "description": "", "hypothesis": "", "context_anchors": None}
        sig_rows = [_Row({"lat": -26.2041, "lng": 28.0473})]
        db = _make_mock_db(case, [], [], sig_rows)
        ctx = build_context(db, 1)
        self.assertEqual(len(ctx["locations"]), 1)

    def test_empty_case_returns_empty_context(self):
        db = _make_mock_db(None, [], [], [])
        ctx = build_context(db, 99)
        self.assertEqual(ctx["actor_ids"], set())
        self.assertEqual(ctx["keywords"], set())
        self.assertEqual(ctx["locations"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
from __future__ import annotations
"""
Sprint 5.4 (2026-08-02) — Conclave critical-path test coverage beyond what
rode along with Sprint 1's fixes.

Sprint 1 tested one specific bug (entity_types merging) in `run_conclave()`
and `materialize_entities()`. This file covers the surrounding behavior of
two Conclave-critical-path components that had zero prior coverage:

  - core/conclave/engine.py's general merge arithmetic (gravity/confidence
    averaging, recommendation majority vote, entity union, empty-input
    default) and run_conclave_with_modules()'s module isolation guarantee
    -- the CLAUDE.md-documented claim that "a crashing module cannot kill
    Flask or block ingestion" was never actually verified by a test.
  - forage/engines/escalation_engine.py's handle_escalation() threshold
    decisions (ESCALATE -> case, MONITOR -> event, below threshold -> no-op,
    idempotency against re-escalating an already-cased signal, and the
    rate-limit-blocks-without-downgrading-to-event behavior).

None of these touch the live database.db -- each builds its own isolated
in-memory sqlite connection or asserts against pure in-repo data structures.

Run:
    python -m pytest tests/test_sprint5_conclave.py -v
    python -m unittest tests.test_sprint5_conclave
"""
import sys
import os
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.conclave.registry import AnalysisResult
from core.conclave.engine import run_conclave, run_conclave_with_modules
import core.conclave.context as conclave_context
from forage.engines.escalation_engine import handle_escalation, ESCALATE_THRESHOLD, MONITOR_THRESHOLD


def _result(entities=None, intent="unknown", gravity=0.0, recommendation="IGNORE",
            confidence=0.0, provenance=None):
    return AnalysisResult(
        entities=entities or [], intent=intent, gravity=gravity,
        recommendation=recommendation, confidence=confidence,
        provenance=provenance or {},
    )


class TestRunConclaveMerging(unittest.TestCase):
    """core/conclave/engine.py::run_conclave() -- general merge arithmetic,
    not just the Sprint 1 entity_types bug."""

    def test_empty_results_returns_ignore_default(self):
        conclusion = run_conclave([])
        self.assertEqual(conclusion.entities, [])
        self.assertEqual(conclusion.recommendation, "IGNORE")
        self.assertEqual(conclusion.gravity, 0.0)
        self.assertEqual(conclusion.confidence, 0.0)

    def test_gravity_and_confidence_are_averaged_not_summed(self):
        results = [
            _result(gravity=0.2, confidence=0.4),
            _result(gravity=0.6, confidence=0.8),
        ]
        conclusion = run_conclave(results)
        self.assertAlmostEqual(conclusion.gravity, 0.4)
        self.assertAlmostEqual(conclusion.confidence, 0.6)

    def test_recommendation_is_majority_vote(self):
        results = [
            _result(recommendation="MONITOR"),
            _result(recommendation="MONITOR"),
            _result(recommendation="ESCALATE"),
        ]
        conclusion = run_conclave(results)
        self.assertEqual(conclusion.recommendation, "MONITOR")

    def test_entities_are_unioned_and_deduplicated(self):
        results = [
            _result(entities=["NPA", "Cape Town"]),
            _result(entities=["NPA", "Pretoria"]),
        ]
        conclusion = run_conclave(results)
        self.assertEqual(set(conclusion.entities), {"NPA", "Cape Town", "Pretoria"})

    def test_intent_is_taken_from_first_result(self):
        results = [
            _result(intent="corruption_probe"),
            _result(intent="unrelated_intent"),
        ]
        conclusion = run_conclave(results)
        self.assertEqual(conclusion.intent, "corruption_probe")

    def test_sources_list_preserves_one_entry_per_result(self):
        results = [_result(provenance={"tag": "a"}), _result(provenance={"tag": "b"})]
        conclusion = run_conclave(results)
        self.assertEqual(len(conclusion.provenance["sources"]), 2)


class TestRunConclaveWithModules(unittest.TestCase):
    """core/conclave/engine.py::run_conclave_with_modules() -- the FMS
    module-isolation guarantee documented in CLAUDE.md ("a crashing module
    cannot kill Flask or block ingestion") had no test verifying it."""

    def _patch_engines(self, engines: dict):
        class _FakeContext:
            def get_engines(self_inner):
                return engines
        original_get_context = conclave_context.get_context
        conclave_context.get_context = lambda: _FakeContext()
        return original_get_context

    def test_no_modules_loaded_matches_plain_run_conclave(self):
        original = self._patch_engines({})
        try:
            core_result = _result(entities=["NPA"], gravity=0.5, confidence=0.5)
            with_modules = run_conclave_with_modules([core_result], {"signal_id": "s1"})
            plain = run_conclave([core_result])
            self.assertEqual(with_modules.entities, plain.entities)
            self.assertEqual(with_modules.gravity, plain.gravity)
            self.assertEqual(with_modules.recommendation, plain.recommendation)
        finally:
            conclave_context.get_context = original

    def test_crashing_module_engine_does_not_propagate_or_break_merge(self):
        def _boom(signal):
            raise RuntimeError("simulated module crash")

        def _good(signal):
            return _result(entities=["SIU"], gravity=0.6, confidence=0.7, recommendation="ESCALATE")

        original = self._patch_engines({"crashy_module": _boom, "good_module": _good})
        try:
            core_result = _result(entities=["NPA"], gravity=0.2, confidence=0.3)
            # Must not raise, even though crashy_module always throws.
            conclusion = run_conclave_with_modules([core_result], {"signal_id": "s1"})
        finally:
            conclave_context.get_context = original

        # The crashing module's absence must not stop the good module's
        # result (or the original core result) from being merged in.
        self.assertIn("NPA", conclusion.entities)
        self.assertIn("SIU", conclusion.entities)

    def test_module_returning_non_analysisresult_is_skipped(self):
        def _wrong_type(signal):
            return {"not": "an AnalysisResult"}

        original = self._patch_engines({"broken_module": _wrong_type})
        try:
            core_result = _result(entities=["NPA"], gravity=0.5, confidence=0.5)
            conclusion = run_conclave_with_modules([core_result], {"signal_id": "s1"})
        finally:
            conclave_context.get_context = original

        # Only the core result should have made it through.
        self.assertEqual(conclusion.entities, ["NPA"])

    def test_context_lookup_failure_falls_back_to_core_results_only(self):
        def _raise_get_context():
            raise RuntimeError("FMS not initialized")
        original = conclave_context.get_context
        conclave_context.get_context = _raise_get_context
        try:
            core_result = _result(entities=["NPA"], gravity=0.5, confidence=0.5)
            conclusion = run_conclave_with_modules([core_result], {"signal_id": "s1"})
        finally:
            conclave_context.get_context = original
        self.assertEqual(conclusion.entities, ["NPA"])


class TestEscalationThresholds(unittest.TestCase):
    """forage/engines/escalation_engine.py::handle_escalation() -- the
    MONITOR/ESCALATE decision boundary and its idempotency/rate-limit
    guards. Zero prior coverage despite being the gate that decides
    whether a signal ever produces an event or a case."""

    def _build_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE cases (
                case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, description TEXT, created_at TEXT,
                auto_generated INTEGER, trigger_signal_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT, description TEXT, created_at TEXT,
                confidence_score REAL, automated INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE signals (
                signal_id TEXT PRIMARY KEY, conclave_meta TEXT
            )
        """)
        conn.execute("INSERT INTO signals (signal_id) VALUES ('sig1')")
        conn.commit()
        return conn

    def test_above_escalate_threshold_creates_case_not_event(self):
        conn = self._build_db()
        conclusion = _result(intent="corruption_probe", gravity=ESCALATE_THRESHOLD,
                              recommendation="ESCALATE", confidence=0.8)
        result_id = handle_escalation(conclusion, "sig1", conn)
        self.assertIsNotNone(result_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        conn.close()

    def test_between_monitor_and_escalate_creates_event_not_case(self):
        conn = self._build_db()
        conclusion = _result(intent="minor_flag", gravity=MONITOR_THRESHOLD,
                              recommendation="MONITOR", confidence=0.5)
        result_id = handle_escalation(conclusion, "sig1", conn)
        self.assertIsNotNone(result_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 0)
        conn.close()

    def test_below_monitor_threshold_creates_nothing(self):
        conn = self._build_db()
        conclusion = _result(intent="noise", gravity=MONITOR_THRESHOLD - 0.01,
                              recommendation="MONITOR", confidence=0.1)
        result_id = handle_escalation(conclusion, "sig1", conn)
        self.assertIsNone(result_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 0)
        conn.close()

    def test_high_gravity_but_ignore_recommendation_creates_nothing(self):
        # Gravity alone must not trigger escalation without a matching
        # recommendation -- both conditions are required.
        conn = self._build_db()
        conclusion = _result(intent="odd_case", gravity=0.9,
                              recommendation="IGNORE", confidence=0.9)
        result_id = handle_escalation(conclusion, "sig1", conn)
        self.assertIsNone(result_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 0)
        conn.close()

    def test_existing_case_for_signal_blocks_re_escalation(self):
        conn = self._build_db()
        conn.execute(
            "INSERT INTO cases (name, trigger_signal_id, auto_generated) VALUES ('Existing', 'sig1', 1)"
        )
        conn.commit()
        conclusion = _result(intent="corruption_probe", gravity=0.9,
                              recommendation="ESCALATE", confidence=0.9)
        result_id = handle_escalation(conclusion, "sig1", conn)
        self.assertIsNone(result_id)
        # Still exactly one case -- no duplicate created.
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 1)
        conn.close()

    def test_rate_limited_escalation_returns_none_without_falling_back_to_event(self):
        # Non-obvious behavior: when ESCALATE-eligible but rate-limited,
        # handle_escalation returns None outright -- it does NOT downgrade
        # to creating an event via the MONITOR branch.
        conn = self._build_db()
        for i in range(5):
            conn.execute(
                "INSERT INTO cases (name, trigger_signal_id, auto_generated, created_at) "
                "VALUES (?, ?, 1, datetime('now'))",
                (f"AutoCase{i}", f"other_sig_{i}"),
            )
        conn.commit()
        conclusion = _result(intent="corruption_probe", gravity=0.9,
                              recommendation="ESCALATE", confidence=0.9)
        result_id = handle_escalation(conclusion, "sig1", conn)
        self.assertIsNone(result_id)
        # Rate limit hit 5 -> no 6th case, and crucially no fallback event either.
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 5)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()

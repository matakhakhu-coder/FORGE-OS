"""What forge_security.sanitizer promises, and what it must never do.

It strips HTML and control characters, collapses whitespace and caps
length. It must not delete words: crime and court reporting is full of
"select", "update", "delete", "drop" and "--". SQL injection is stopped
by parameterised queries, which the last tests check directly.
"""
import os
import sqlite3
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from forge_security.sanitizer import (  # noqa: E402
    MAX_TEXT_LENGTH,
    sanitize_html_fragment,
    sanitize_signal_text,
)

NEWS_LINES = [
    "Police select suspects in Delft",
    "Man shot dead -- police",
    "SAPS will update the family; no arrests yet",
    "Court to delete charges against two accused",
    "Union leaders drop demands after taxi violence talks",
    "Create a safe route, says Steenberg CPF chair",
    "Contact @SAPoliceService or Crime Stop on 08600 10111",
    "Killing cast a shadow over Exec meeting in Athlone",
    "He was sleeping when gunmen entered; 1 = 1 is not a clue",
]


class TestKeepsNewsText(unittest.TestCase):
    def test_ordinary_news_text_is_unchanged(self):
        for line in NEWS_LINES:
            with self.subTest(line=line):
                self.assertEqual(sanitize_signal_text(line), line)

    def test_never_inserts_redacted(self):
        for line in NEWS_LINES:
            self.assertNotIn("[REDACTED]", sanitize_signal_text(line))
            self.assertNotIn("[REDACTED]", sanitize_html_fragment(line))

    def test_html_fragment_keeps_words_and_allowed_tags(self):
        out = sanitize_html_fragment("<p>Police <b>select</b> suspects -- SAPS</p>")
        self.assertEqual(out, "<p>Police <b>select</b> suspects -- SAPS</p>")


class TestStillCleans(unittest.TestCase):
    def test_strips_script_tags(self):
        out = sanitize_signal_text("Delft <script>alert('x')</script>shooting")
        self.assertNotIn("<script", out)
        self.assertNotIn("</script", out)
        self.assertIn("Delft", out)

    def test_output_is_html_safe(self):
        out = sanitize_signal_text('5 < 6 & <img src=x onerror=alert(1)>')
        self.assertNotIn("<", out)
        self.assertNotIn("onerror", out)

    def test_html_fragment_drops_dangerous_markup(self):
        out = sanitize_html_fragment(
            '<p onclick="x()">ok</p><script>bad()</script><iframe src=x></iframe>'
            '<a href="javascript:alert(1)">l</a>'
        )
        self.assertNotIn("onclick", out)
        self.assertNotIn("<script", out)
        self.assertNotIn("<iframe", out)
        self.assertNotIn("javascript:", out)

    def test_strips_control_characters(self):
        self.assertEqual(sanitize_signal_text("Lang\x00a\x07 shooting"), "Langa shooting")

    def test_collapses_whitespace(self):
        self.assertEqual(sanitize_signal_text("  two\n\n  dead\t "), "two dead")

    def test_none_and_non_strings(self):
        self.assertEqual(sanitize_signal_text(None), "")
        self.assertEqual(sanitize_signal_text(14), "14")

    def test_truncates(self):
        self.assertEqual(len(sanitize_signal_text("a" * (MAX_TEXT_LENGTH + 50))), MAX_TEXT_LENGTH)


class TestDatabaseLayerStopsInjection(unittest.TestCase):
    """The real protection: values are bound as parameters."""

    PAYLOAD = "Robert'); DROP TABLE signals;--"

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, title TEXT)")

    def tearDown(self):
        self.conn.close()

    def test_parameterised_insert_stores_payload_as_text(self):
        title = sanitize_signal_text(self.PAYLOAD)
        self.assertEqual(title, self.PAYLOAD)
        self.conn.execute("INSERT INTO signals (title) VALUES (?)", (title,))
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertIn("signals", tables)
        self.assertEqual(self.conn.execute("SELECT title FROM signals").fetchone(), (self.PAYLOAD,))

    def test_nexus_bridge_flush_stores_payload_as_text(self):
        from tools.nexus_bridge import flush_to_db
        self.conn.execute(
            "CREATE TABLE entity_relationships (subject_actor_id INTEGER, "
            "object_actor_id INTEGER, relation_type TEXT, confidence REAL, "
            "description TEXT, UNIQUE(subject_actor_id, object_actor_id, relation_type))"
        )
        cur = self.conn.cursor()
        desc = sanitize_signal_text(f"Joint appearance in: {self.PAYLOAD}")
        flush_to_db(cur, [(1, 2, "co_occurrence", 0.85, desc)], dry_run=False)
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertIn("signals", tables)
        self.assertEqual(
            self.conn.execute("SELECT description FROM entity_relationships").fetchone(),
            ("Joint appearance in: " + self.PAYLOAD,),
        )


class TestToolsUseSharedCleaner(unittest.TestCase):
    def test_no_private_copies(self):
        from forge_security import sanitizer
        from tools import coalition_interceptor, nexus_bridge
        self.assertIs(coalition_interceptor.sanitize_signal_text, sanitizer.sanitize_signal_text)
        self.assertIs(nexus_bridge.sanitize_signal_text, sanitizer.sanitize_signal_text)

    def test_run_as_script_still_gets_shared_cleaner(self):
        """`python tools/x.py` puts tools/ first on sys.path, not the repo root.
        Before this fix that silently switched to the word-deleting copy."""
        for mod in ("coalition_interceptor", "nexus_bridge"):
            code = (
                "import sys; sys.path=[p for p in sys.path if p not in ('', %r)];"
                "sys.path.insert(0, %r); import %s as m;"
                "print(m.sanitize_signal_text.__module__)"
            ) % (REPO, os.path.join(REPO, "tools"), mod)
            out = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True,
                cwd=os.path.join(REPO, "tools"), timeout=60,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "forge_security.sanitizer", out.stderr)


if __name__ == "__main__":
    unittest.main()

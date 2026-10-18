import unittest
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.coalition_interceptor import sanitize_signal_text, dynamic_insert

class TestV7Interceptor(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.cursor = self.conn.cursor()
        self.cursor.execute("CREATE TABLE mock_events (case_id INTEGER, event_id INTEGER)")
        
    def tearDown(self):
        self.conn.close()

    def test_sanitizer_advanced_sqli(self):
        """An injection-looking title is stored as inert text, not executed.

        The cleaner keeps the words (it is not a SQL filter); safety comes
        from the parameterised insert. The original April test asserted
        that SLEEP and -- were deleted, which only a removed local
        fallback cleaner ever did.
        """
        dirty_title = "ActionSA Press Release'; SLEEP(5)--"
        clean = sanitize_signal_text(dirty_title)
        self.assertEqual(clean, dirty_title)
        self.cursor.execute("CREATE TABLE mock_titles (title TEXT)")
        dynamic_insert(self.cursor, "mock_titles", {"title": clean})
        self.cursor.execute("SELECT title FROM mock_titles")
        self.assertEqual(self.cursor.fetchone(), (dirty_title,))

    def test_uses_shared_sanitizer(self):
        """No private copy of the cleaner hides in this tool any more."""
        from forge_security import sanitizer
        self.assertIs(sanitize_signal_text, sanitizer.sanitize_signal_text)

    def test_dynamic_insert_strict_concat(self):
        """Gap 1 Fix: Schema mapping strictly uses concatenation."""
        payload = {"case_id": 36, "event_id": 999}
        dynamic_insert(self.cursor, "mock_events", payload)
        self.cursor.execute("SELECT * FROM mock_events")
        self.assertEqual(self.cursor.fetchone(), (36, 999))

if __name__ == '__main__':
    unittest.main()
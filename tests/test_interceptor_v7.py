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
        """Gap 2 Fix: Time-based blind and encoding scrubbed."""
        dirty_title = "ActionSA Press Release'; SLEEP(5)--"
        clean = sanitize_signal_text(dirty_title)
        self.assertNotIn("SLEEP", clean)
        self.assertNotIn("--", clean)

    def test_dynamic_insert_strict_concat(self):
        """Gap 1 Fix: Schema mapping strictly uses concatenation."""
        payload = {"case_id": 36, "event_id": 999}
        dynamic_insert(self.cursor, "mock_events", payload)
        self.cursor.execute("SELECT * FROM mock_events")
        self.assertEqual(self.cursor.fetchone(), (36, 999))

if __name__ == '__main__':
    unittest.main()
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.nexus_bridge import sanitize_signal_text, extract_safe_sentence

class TestNexusV5(unittest.TestCase):
    
    def test_sql_injection_sanitization(self):
        """Validates payload stripping."""
        payload = "Epstein <script>alert('xss')</script> flight; DROP TABLE actors;"
        clean = sanitize_signal_text(payload)
        self.assertNotIn("<script>", clean)
        
    def test_ram_safe_extraction(self):
        """Validates text extraction doesn't exceed 250 chars."""
        massive_text = "A. " + "word " * 1000 + "TargetName" + " word" * 1000 + " .B"
        match_start = massive_text.find("TargetName")
        match_end = match_start + 10
        snippet = extract_safe_sentence(massive_text, match_start, match_end)
        self.assertTrue(len(snippet) <= 250)

    def test_dynamic_boundary(self):
        """Validates exact sentence capture."""
        text = "Irrelevant stuff. This is the TargetName sentence. More noise."
        match_start = text.find("TargetName")
        snippet = extract_safe_sentence(text, match_start, match_start+10)
        self.assertEqual(snippet, "This is the TargetName sentence.")

if __name__ == '__main__':
    unittest.main()
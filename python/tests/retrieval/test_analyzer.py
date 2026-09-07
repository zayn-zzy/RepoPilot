"""QueryAnalyzer tests — tokenization, symbol/path hint extraction, kind
hint detection."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.retrieval import QueryAnalyzer  # noqa: E402


class TestQueryAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = QueryAnalyzer()

    def test_terms_lowercased_deduped(self):
        q = self.analyzer.analyze("Fix the bug in bug tracking")
        self.assertEqual(q.terms, ["fix", "bug", "tracking"])

    def test_stopwords_filtered_from_terms_but_not_identifiers(self):
        q = self.analyzer.analyze("how to limit agent spending and turns")
        self.assertEqual(q.terms, ["limit", "agent", "spending", "turns"])
        self.assertIn("how", q.identifiers)  # identifiers keep everything

    def test_symbol_hints_from_camel_and_snake(self):
        q = self.analyzer.analyze("Refactor check_permission in ToolRegistry")
        self.assertIn("check_permission", q.symbol_hints)
        self.assertIn("ToolRegistry", q.symbol_hints)
        self.assertNotIn("refactor", q.symbol_hints)  # lowercase word, not a hint

    def test_quoted_strings_become_hints(self):
        q = self.analyzer.analyze('look for "mini_claude.agent.Agent" definition')
        self.assertIn("mini_claude.agent.Agent", q.symbol_hints)

    def test_path_hints(self):
        q = self.analyzer.analyze("read mini_claude/agent.py and mini_claude.tools")
        self.assertIn("mini_claude/agent.py", q.path_hints)
        self.assertIn("mini_claude.tools", q.path_hints)

    def test_kind_hint(self):
        self.assertEqual(self.analyzer.analyze("find the User class").kind_hint, "class")
        self.assertEqual(self.analyzer.analyze("where is method greet").kind_hint, "method")
        self.assertEqual(self.analyzer.analyze("function add").kind_hint, "function")
        self.assertIsNone(self.analyzer.analyze("show me the database").kind_hint)

    def test_kind_words_excluded_from_terms(self):
        q = self.analyzer.analyze("find the class User")
        self.assertNotIn("class", q.terms)

    def test_empty_query(self):
        q = self.analyzer.analyze("")
        self.assertEqual(q.terms, [])
        self.assertEqual(q.symbol_hints, [])

    def test_identifiers_keep_original_case(self):
        q = self.analyzer.analyze("Processor and processor")
        self.assertEqual(q.identifiers, ["Processor", "and", "processor"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

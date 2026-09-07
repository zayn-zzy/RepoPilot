"""ContextBuilder tests — token budget adherence, full-content fill,
symbol-summary fallback, and overflow notes."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import RepositoryIndex  # noqa: E402
from mini_claude.retrieval import ContextBuilder, estimate_tokens  # noqa: E402
from mini_claude.retrieval.model import RetrievalHit  # noqa: E402


def _h(path, score=1.0, symbols=None):
    return RetrievalHit(path, score, symbols=symbols or [])


class TestEstimateTokens(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(estimate_tokens("aaaa"), 1)
        self.assertEqual(estimate_tokens(""), 0)

    def test_multibyte(self):
        # 3-byte UTF-8 chars count ~1 token each at 4 bytes/token
        self.assertEqual(estimate_tokens("中文"), 2)


class TestContextBuilder(unittest.TestCase):
    def setUp(self):
        self.files = {
            "a.py": "def alpha():\n    return 1\n" * 3,          # ~57 tokens
            "b.py": "def beta():\n    return 2\n" * 3,
            "c.py": "def gamma():\n    return 3\n" * 3,
        }

    def _builder(self, index=None):
        return ContextBuilder(self.files, index)

    def test_stays_within_budget(self):
        ctx = self._builder().build_context([_h("a.py"), _h("b.py")], token_budget=100)
        self.assertLessEqual(estimate_tokens(ctx), 100)
        self.assertIn("### a.py", ctx)
        self.assertIn("def alpha", ctx)

    def test_full_files_fit_when_budget_allows(self):
        ctx = self._builder().build_context([_h("a.py")], token_budget=500)
        self.assertIn("def alpha", ctx)

    def test_over_budget_files_omitted(self):
        # a.py ≈ 27 tokens fits in 40; a.py + b.py ≈ 54 does not.
        ctx = self._builder().build_context([_h("a.py"), _h("b.py")], token_budget=40)
        self.assertIn("a.py", ctx)
        self.assertNotIn("b.py", ctx)

    def test_overflow_note_when_budget_permits(self):
        ctx = self._builder().build_context([_h("a.py"), _h("b.py")], token_budget=45)
        self.assertIn("omitted", ctx)

    def test_symbol_summary_fallback_with_index(self):
        """When the full file exceeds the budget, the builder degrades to a
        signature summary instead of dropping the file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "big.py").write_text(
                "def first():\n    pass\n\n" + "x = 1\n" * 500 + "\ndef last():\n    pass\n"
            )
            idx = RepositoryIndex(root)
            idx.build()
            content = (root / "big.py").read_text()
            builder = ContextBuilder({"big.py": content}, idx)
            ctx = builder.build_context([_h("big.py")], token_budget=100)
            self.assertLessEqual(estimate_tokens(ctx), 100)
            self.assertIn("def first()", ctx)   # symbol summary line
            self.assertNotIn("x = 1", ctx)      # body not included

    def test_empty_hits(self):
        self.assertEqual(self._builder().build_context([], token_budget=100), "")

    def test_tiny_budget_empty_result(self):
        ctx = self._builder().build_context([_h("a.py")], token_budget=5)
        self.assertEqual(ctx, "")

    def test_max_file_tokens_caps_single_file(self):
        """max_file_tokens bounds even a single file — content over the cap
        degrades to the summary (none without an index) and is omitted."""
        builder = ContextBuilder(self.files, max_file_tokens=20)
        ctx = builder.build_context([_h("a.py")], token_budget=5000)
        self.assertLessEqual(estimate_tokens(ctx), 5000)
        # no index -> no summary -> only the overflow note remains
        self.assertNotIn("def alpha", ctx)
        self.assertIn("omitted", ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)

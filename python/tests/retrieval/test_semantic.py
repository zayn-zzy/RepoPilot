"""SemanticRetriever (LSA) tests — cosine similarity over latent vectors,
determinism, and edge cases."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.retrieval import SemanticRetriever  # noqa: E402
from mini_claude.retrieval.analyzer import QueryAnalyzer  # noqa: E402

FILES = {
    "auth.py": "def login(user, password):\n    return authenticate(user, password)\n" * 4,
    "db.py": "def connect():\n    return Database().open()\n" * 4,
    "doc_auth.md": "authentication login password session",
}
ANALYZER = QueryAnalyzer()


class TestSemantic(unittest.TestCase):
    def test_similar_file_ranks_first(self):
        ret = SemanticRetriever(FILES)
        hits = ret.search(ANALYZER.analyze("how does user login work"), top_k=3)
        self.assertEqual(hits[0].file_path, "auth.py")

    def test_returns_requested_top_k(self):
        ret = SemanticRetriever(FILES)
        self.assertEqual(len(ret.search("login password", top_k=2)), 2)

    def test_deterministic(self):
        a = SemanticRetriever(FILES).search("login password")
        b = SemanticRetriever(FILES).search("login password")
        self.assertEqual([h.file_path for h in a], [h.file_path for h in b])
        self.assertEqual([round(h.score, 10) for h in a], [round(h.score, 10) for h in b])

    def test_empty_corpus(self):
        self.assertEqual(SemanticRetriever({}).search("x"), [])

    def test_empty_query(self):
        ret = SemanticRetriever(FILES)
        self.assertEqual(ret.search(""), [])

    def test_plain_string_query(self):
        # "login password" is densely auth-related: both auth.py and the
        # auth-topic doc are legitimately closer than the unrelated db.py.
        ret = SemanticRetriever(FILES)
        hits = ret.search("login password")
        self.assertIn(hits[0].file_path, {"auth.py", "doc_auth.md"})
        self.assertEqual(hits[-1].file_path, "db.py")

    def test_sources_recorded(self):
        ret = SemanticRetriever(FILES)
        self.assertIn("semantic", ret.search("login")[0].sources)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""StructuralRetriever tests — symbol matching and 1-hop/2-hop dependency
expansion over the Phase 2 fixture repository."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import RepositoryIndex  # noqa: E402
from mini_claude.retrieval import StructuralRetriever  # noqa: E402
from mini_claude.retrieval.analyzer import QueryAnalyzer  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"
ANALYZER = QueryAnalyzer()


class StructuralTestBase(unittest.TestCase):
    def setUp(self):
        self.idx = RepositoryIndex(FIXTURE)
        self.idx.build()
        self.ret = StructuralRetriever(self.idx)


class TestSymbolMatch(StructuralTestBase):
    def test_exact_symbol_hint_finds_file(self):
        hits = self.ret.search(ANALYZER.analyze("where is User"), top_k=5)
        self.assertEqual(hits[0].file_path, "pkg/models.py")
        self.assertIn("pkg.models.User", hits[0].symbols)

    def test_substring_match_weaker_than_exact(self):
        exact = self.ret.search(ANALYZER.analyze("User"))
        self.assertEqual(exact[0].file_path, "pkg/models.py")
        # "User" is a substring of nothing else here — base class BaseModel
        # doesn't contain it, so models.py stays top via exact match only.
        self.assertEqual(exact[0].symbols, ["pkg.models.User"])

    def test_no_symbol_hints_returns_empty(self):
        self.assertEqual(self.ret.search(ANALYZER.analyze("please refactor")), [])

    def test_plain_string_query_uses_tokens_as_hints(self):
        hits = self.ret.search("Processor")
        self.assertEqual(hits[0].file_path, "pkg/core.py")


class TestExpansion(StructuralTestBase):
    def test_hop1_adds_dependencies_and_dependents(self):
        """User matches pkg/models.py; its 1-hop includes core.py (mutual
        import), __init__.py (imports models), and models' own deps."""
        hits = self.ret.search(ANALYZER.analyze("User"), top_k=20, max_hop=1)
        paths = {h.file_path for h in hits}
        self.assertIn("pkg/models.py", paths)     # seed
        self.assertIn("pkg/core.py", paths)       # dependency (mutual)
        self.assertIn("pkg/__init__.py", paths)   # dependent
        self.assertNotIn("pkg/sub/helper.py", paths)  # only 2-hop away via core

    def test_hop2_reaches_transitive_files(self):
        hits = self.ret.search(ANALYZER.analyze("User"), top_k=20, max_hop=2)
        paths = {h.file_path for h in hits}
        self.assertIn("pkg/sub/helper.py", paths)     # helper -> core (1) -> models (2)
        self.assertIn("pkg/utils.py", paths)          # via core

    def test_seed_ranks_above_expanded(self):
        hits = self.ret.search(ANALYZER.analyze("User"), top_k=20, max_hop=2)
        ranks = {h.file_path: i for i, h in enumerate(hits)}
        self.assertLess(ranks["pkg/models.py"], ranks["pkg/sub/helper.py"])

    def test_max_hop_zero_no_expansion(self):
        hits = self.ret.search(ANALYZER.analyze("User"), top_k=20, max_hop=0)
        self.assertEqual({h.file_path for h in hits}, {"pkg/models.py"})

    def test_expand_distances(self):
        dist = self.ret.expand(["pkg/models.py"], hops=2)
        self.assertEqual(dist["pkg/models.py"], 0)
        self.assertEqual(dist["pkg/core.py"], 1)
        self.assertEqual(dist["pkg/sub/helper.py"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

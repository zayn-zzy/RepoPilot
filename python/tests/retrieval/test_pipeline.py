"""HybridRetriever pipeline tests — end-to-end on the fixture repository,
retriever toggling (benchmark configurations), and grep baseline."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import RepositoryIndex  # noqa: E402
from mini_claude.retrieval.embedding import LSABackend
from mini_claude.retrieval import HybridRetriever, grep_baseline  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"


def _files():
    idx = RepositoryIndex(FIXTURE)
    idx.build()
    return {p: (FIXTURE / p).read_text() for p in idx.files()}


class TestGrepBaseline(unittest.TestCase):
    def test_grep_finds_occurrences(self):
        files = _files()
        hits = grep_baseline(files, "greet")
        self.assertIn("pkg/models.py", [h.file_path for h in hits])

    def test_grep_no_match(self):
        self.assertEqual(grep_baseline(_files(), "zzz_nothing"), [])

    def test_grep_top_k(self):
        self.assertEqual(len(grep_baseline(_files(), "return", top_k=4)), 4)


class TestHybridPipeline(unittest.TestCase):
    def setUp(self):
        self.idx = RepositoryIndex(FIXTURE)
        self.idx.build()
        self.hy = HybridRetriever(self.idx, semantic_backend=LSABackend())

    def test_symbol_query_finds_defining_file(self):
        hits = self.hy.retrieve("where is the Processor class defined")
        self.assertEqual(hits[0].file_path, "pkg/core.py")

    def test_method_query_lands_on_models(self):
        hits = self.hy.retrieve("the greet method on User")
        self.assertIn("pkg/models.py", [h.file_path for h in hits[:3]])

    def test_top_k_respected(self):
        self.assertEqual(len(self.hy.retrieve("helper", top_k=5)), 5)

    def test_path_hint_query(self):
        hits = self.hy.retrieve("read pkg/utils.py")
        self.assertIn("pkg/utils.py", [h.file_path for h in hits[:2]])

    def test_build_context_within_budget(self):
        from mini_claude.retrieval import estimate_tokens

        ctx = self.hy.build_context("Processor class", token_budget=1500)
        self.assertLessEqual(estimate_tokens(ctx), 1500)
        self.assertIn("pkg/core.py", ctx)

    def test_modes_construct_all_benchmark_configs(self):
        # Semantic only (Baseline B)
        semantic_only = HybridRetriever(self.idx, enable_lexical=False, enable_structural=False,
                                            semantic_backend=LSABackend())
        self.assertIsNotNone(semantic_only.semantic)
        self.assertIsNone(semantic_only.lexical)
        # Hybrid without graph (Baseline C)
        no_graph = HybridRetriever(self.idx, enable_structural=False,
                                     semantic_backend=LSABackend())
        hits = no_graph.retrieve("User class")
        self.assertIn("pkg/models.py", [h.file_path for h in hits[:5]])
        # Full hybrid + graph (Proposed)
        hits_full = self.hy.retrieve("User class")
        full_paths = {h.file_path for h in hits_full}
        self.assertIn("pkg/models.py", full_paths)

    def test_deterministic_across_instances(self):
        a = self.hy.retrieve("Processor")
        idx_b = RepositoryIndex(FIXTURE)
        idx_b.build()
        b = HybridRetriever(idx_b, semantic_backend=LSABackend()).retrieve("Processor")
        # separately built index — same content, same ranking
        self.assertEqual([h.file_path for h in a], [h.file_path for h in b])


if __name__ == "__main__":
    unittest.main(verbosity=2)

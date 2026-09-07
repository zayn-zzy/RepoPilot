"""Fusion and reranker tests — RRF merge, weighted fusion, content rerank."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.retrieval import Reranker, reciprocal_rank_fusion, weighted_fusion  # noqa: E402
from mini_claude.retrieval.analyzer import QueryAnalyzer  # noqa: E402
from mini_claude.retrieval.model import RetrievalHit  # noqa: E402

ANALYZER = QueryAnalyzer()


def _h(path, score, source="a", symbols=None):
    return RetrievalHit(path, score, sources={source: score}, symbols=symbols or [])


class TestRRF(unittest.TestCase):
    def test_merge_and_rank(self):
        fused = reciprocal_rank_fusion({
            "lexical": [_h("x.py", 9.0), _h("y.py", 8.0)],
            "semantic": [_h("y.py", 0.9), _h("z.py", 0.8)],
        })
        self.assertEqual([h.file_path for h in fused], ["y.py", "x.py", "z.py"])
        # y.py appears in both lists -> double RRF contribution
        self.assertGreater(fused[0].score, fused[1].score)

    def test_sources_accumulate(self):
        fused = reciprocal_rank_fusion({
            "lexical": [_h("x.py", 1.0)],
            "semantic": [_h("x.py", 0.5)],
        })
        self.assertEqual(set(fused[0].sources), {"lexical", "semantic"})

    def test_symbols_merged_deduped(self):
        fused = reciprocal_rank_fusion({
            "lexical": [_h("x.py", 1.0, symbols=["a.b"])],
            "semantic": [_h("x.py", 0.5, symbols=["a.b", "a.c"])],
        })
        self.assertEqual(fused[0].symbols, ["a.b", "a.c"])

    def test_weights_apply(self):
        # A zero weight strips the contribution but the file may still enter
        # via another retriever — here only lexical lists x.py, so it must
        # rank with a zero score but still exist.
        zero = reciprocal_rank_fusion({"lexical": [_h("x.py", 1.0)]}, weights={"lexical": 0.0})
        self.assertEqual(zero[0].file_path, "x.py")
        self.assertEqual(zero[0].score, 0.0)
        # Heavier lexical weight flips the ranking.
        fused = reciprocal_rank_fusion({
            "lexical": [_h("x.py", 1.0)],
            "semantic": [_h("y.py", 1.0)],
        }, weights={"lexical": 10.0, "semantic": 1.0})
        self.assertEqual(fused[0].file_path, "x.py")

    def test_top_k(self):
        fused = reciprocal_rank_fusion(
            {"a": [_h(f"{i}.py", 1.0) for i in range(5)]}, top_k=3
        )
        self.assertEqual(len(fused), 3)

    def test_empty_input(self):
        self.assertEqual(reciprocal_rank_fusion({}), [])


class TestWeightedFusion(unittest.TestCase):
    def test_normalizes_score_scales(self):
        # lexical scores are huge, semantic tiny — normalization must not let
        # the scale difference dominate the weighted sum. After min-max
        # normalization both sources are on [0, 1]; the semantic weight 2.0
        # makes y.py (top semantic, bottom lexical) beat x.py (the reverse).
        fused = weighted_fusion({
            "lexical": [_h("x.py", 1000.0), _h("y.py", 900.0)],
            "semantic": [_h("y.py", 0.9), _h("z.py", 0.1)],
        }, weights={"lexical": 1.0, "semantic": 2.0})
        self.assertEqual(fused[0].file_path, "y.py")

    def test_empty_lists_ignored(self):
        fused = weighted_fusion({"lexical": [_h("x.py", 1.0)], "semantic": []})
        self.assertEqual([h.file_path for h in fused], ["x.py"])


class TestReranker(unittest.TestCase):
    FILES = {
        "a.py": "def check_permission():\n    return allow\n",
        "b.py": "totally unrelated content here\n",
    }

    def test_symbol_bonus_lifts_defining_file(self):
        import tempfile

        from mini_claude.repo import RepositoryIndex

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text(self.FILES["a.py"])
            (root / "b.py").write_text(self.FILES["b.py"])
            idx = RepositoryIndex(root)
            idx.build()
            reranker = Reranker(self.FILES, idx)
            candidates = [_h("b.py", 1.0), _h("a.py", 1.0)]
            out = reranker.rerank(ANALYZER.analyze("check_permission"), candidates)
            self.assertEqual(out[0].file_path, "a.py")

    def test_rerank_respects_top_k(self):
        reranker = Reranker(self.FILES)
        out = reranker.rerank(ANALYZER.analyze("permission"), [_h("a.py", 1.0), _h("b.py", 1.0)], top_k=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].file_path, "a.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

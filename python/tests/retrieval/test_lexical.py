"""LexicalRetriever (BM25) tests."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.retrieval import LexicalRetriever  # noqa: E402
from mini_claude.retrieval.analyzer import QueryAnalyzer  # noqa: E402

FILES = {
    "a.py": "def calculate_tax(price):\n    return price * 0.2\n" * 5,
    "b.py": "def render_page():\n    return '<html>'\n",
    "c.py": "calculate_tax calculate_tax calculate_tax",
}
ANALYZER = QueryAnalyzer()


class TestLexical(unittest.TestCase):
    def test_query_terms_rank_matching_file_first(self):
        # BM25's length normalization favors the DENSE file (c.py is 3 tokens
        # of pure matches) over the longer one — both must outrank b.py.
        ret = LexicalRetriever(FILES)
        hits = ret.search(ANALYZER.analyze("calculate_tax"))
        self.assertEqual(hits[0].file_path, "c.py")
        self.assertEqual(hits[1].file_path, "a.py")
        self.assertNotIn("b.py", [h.file_path for h in hits])

    def test_irrelevant_query_returns_empty(self):
        ret = LexicalRetriever(FILES)
        self.assertEqual(ret.search(ANALYZER.analyze("zzz_nothing")), [])

    def test_empty_query_returns_empty(self):
        ret = LexicalRetriever(FILES)
        self.assertEqual(ret.search(""), [])

    def test_plain_string_query_works(self):
        ret = LexicalRetriever(FILES)
        hits = ret.search("render_page")
        self.assertEqual(hits[0].file_path, "b.py")

    def test_top_k_respected(self):
        ret = LexicalRetriever(FILES)
        hits = ret.search(ANALYZER.analyze("calculate_tax render_page"), top_k=2)
        self.assertEqual(len(hits), 2)

    def test_sources_recorded(self):
        ret = LexicalRetriever(FILES)
        hits = ret.search(ANALYZER.analyze("calculate_tax"))
        self.assertIn("lexical", hits[0].sources)

    def test_empty_corpus(self):
        ret = LexicalRetriever({})
        self.assertEqual(ret.search("anything"), [])

    def test_deterministic(self):
        hits_a = LexicalRetriever(FILES).search(ANALYZER.analyze("calculate_tax"))
        hits_b = LexicalRetriever(FILES).search(ANALYZER.analyze("calculate_tax"))
        self.assertEqual([h.file_path for h in hits_a], [h.file_path for h in hits_b])


class TestLexicalSymbolField(unittest.TestCase):
    def test_symbol_field_boost(self):
        """With a repository index, identifier queries must surface the
        defining file — the symbol-name field outweighs a single mention
        in another file's body."""
        import tempfile

        from mini_claude.repo import RepositoryIndex

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "defs.py").write_text("def special_calc():\n    return 1\n")
            (root / "uses.py").write_text("import defs\nspecial_calc()\n")
            idx = RepositoryIndex(root)
            idx.build()
            files = {p: (root / p).read_text() for p in idx.files()}
            ret = LexicalRetriever(files, symbol_index=idx)
            hits = ret.search(ANALYZER.analyze("special_calc"))
            self.assertEqual(hits[0].file_path, "defs.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

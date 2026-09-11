"""Chinese retrieval tests — jieba/bigram tokenization, the query
analyzer, BM25 over Chinese comments, the grep baseline, and the LSA
bigram preprocessing. A pure-Chinese query used to produce ZERO tokens
and an empty retrieval; these tests pin the fixed behavior."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.retrieval import (  # noqa: E402
    LexicalRetriever,
    NeuralSemanticRetriever,
    QueryAnalyzer,
    grep_baseline,
)
from mini_claude.retrieval.chinese import (  # noqa: E402
    expand_cjk_bigrams,
    tokenize_chinese,
    zh_method,
)
from mini_claude.retrieval.embedding import LSABackend  # noqa: E402

FILES = {
    "auth.py": "# 登录功能：检查用户密码是否正确\n"
               "def login(user, password):\n    return check(user, password)\n",
    "calc.py": "def multiply(a, b):\n    return a * b\n",
    "docs.md": "订单总价在 pricing.py 中计算，折扣逻辑在 discount 模块。\n",
}


class TestChineseTokenize(unittest.TestCase):
    def test_jieba_words_with_stopwords_dropped(self):
        if zh_method() != "jieba":
            self.skipTest("jieba not installed")
        toks = tokenize_chinese("订单总价在哪里计算")
        self.assertEqual(toks, ["订单", "总价", "哪里", "计算"])  # 在 = stopword

    def test_bigram_fallback(self):
        self.assertEqual(tokenize_chinese("登录功能", method="bigram"),
                         ["登录", "录功", "功能"])

    def test_mixed_text_only_touches_cjk(self):
        toks = tokenize_chinese("call 权限检查() on this")
        self.assertIn("权限", toks)
        self.assertNotIn("call", toks)  # ASCII is the other tokenizer's job

    def test_expand_bigrams(self):
        # a 2-char word's only bigram is itself; longer runs split
        self.assertEqual(expand_cjk_bigrams("价格x计算"), "价格x计算")
        self.assertEqual(expand_cjk_bigrams("登录功能"), "登录 录功 功能")

    def test_no_jieba_fallback_is_honest(self):
        import mini_claude.retrieval.chinese as zh
        saved = zh._HAS_JIEBA
        zh._HAS_JIEBA = False
        try:
            self.assertEqual(zh_method(), "bigram")
            self.assertEqual(tokenize_chinese("登录"), ["登录"])
        finally:
            zh._HAS_JIEBA = saved


class TestChineseQueryAnalyzer(unittest.TestCase):
    def test_pure_chinese_query_has_terms(self):
        q = QueryAnalyzer().analyze("订单总价在哪里计算")
        self.assertTrue(q.terms)               # was [] before Phase 15
        self.assertIn("订单", q.terms)
        self.assertEqual(q.zh_method, "jieba")

    def test_mixed_query_keeps_both(self):
        q = QueryAnalyzer().analyze("check_permission 权限检查")
        self.assertIn("check_permission", q.terms)
        self.assertIn("权限", q.terms)

    def test_english_only_query_has_empty_zh_method(self):
        q = QueryAnalyzer().analyze("fix the multiply bug")
        self.assertEqual(q.zh_method, "")


class TestChineseLexical(unittest.TestCase):
    def test_bm25_finds_chinese_comments(self):
        ret = LexicalRetriever(FILES)
        hits = ret.search(QueryAnalyzer().analyze("登录功能在哪里"))
        self.assertTrue(hits)
        self.assertEqual(hits[0].file_path, "auth.py")

    def test_bm25_pure_chinese_top_hit(self):
        ret = LexicalRetriever(FILES)
        hits = ret.search(QueryAnalyzer().analyze("订单总价怎么算"))
        self.assertEqual(hits[0].file_path, "docs.md")

    def test_grep_baseline_matches_chinese(self):
        hits = grep_baseline(FILES, "登录功能")
        self.assertEqual([h.file_path for h in hits], ["auth.py"])
        hits = grep_baseline(FILES, "折扣")
        self.assertEqual([h.file_path for h in hits], ["docs.md"])

    def test_lsa_backend_sees_chinese(self):
        backend = LSABackend()
        ret = NeuralSemanticRetriever(FILES, backend=backend)
        hits = ret.search("登录功能")
        self.assertTrue(hits)
        self.assertEqual(hits[0].file_path, "auth.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

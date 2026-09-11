"""Neural semantic retrieval tests — embedding backends (fake /
API-shape / LSA), the NeuralSemanticRetriever (ranking, labeling,
disk cache), backend detection and the HybridRetriever wiring. The real
fastembed model is exercised only in the manual acceptance, never here
(tests must not download model weights)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.retrieval import (  # noqa: E402
    HybridRetriever,
    NeuralSemanticRetriever,
    detect_embedding_backend,
)
from mini_claude.retrieval.embedding import (  # noqa: E402
    APIEmbeddingBackend,
    LSABackend,
)

FILES = {
    "calc.py": "def multiply(a, b):\n    return a * b\n",
    "auth.py": "def login(user, password):\n    return check_credentials(user)\n",
    "docs.md": "This file documents the system architecture.\n",
}


class _FakeBackend:
    """Deterministic 2-d vectors by exact text — counts every embed call."""

    label = "fake(2d)"

    def __init__(self):
        self.calls = 0
        self._vectors = {
            FILES["calc.py"]: [1.0, 0.0],
            FILES["auth.py"]: [0.0, 1.0],
            FILES["docs.md"]: [0.7, 0.7],
        }
        self._query = [0.0, 1.0]

    def embed(self, texts):
        self.calls += 1
        return [self._vectors.get(t, self._query) for t in texts]


class TestNeuralRetriever(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_ranking_by_cosine_and_label(self):
        backend = _FakeBackend()
        ret = NeuralSemanticRetriever(FILES, backend=backend)
        hits = ret.search("login")
        self.assertEqual(hits[0].file_path, "auth.py")   # query == auth vec
        self.assertEqual(hits[0].sources["backend"], "fake(2d)")
        self.assertTrue(all(h.score > 0 for h in hits))
        self.assertEqual(ret.label, "fake(2d)")

    def test_empty_corpus(self):
        ret = NeuralSemanticRetriever({}, backend=_FakeBackend())
        self.assertEqual(ret.search("x"), [])

    def test_cache_saved_and_reused_without_reembedding(self):
        cache = Path(self._tmp.name) / "emb.json"
        backend = _FakeBackend()
        ret = NeuralSemanticRetriever(FILES, backend=backend, cache_path=cache)
        first = ret.search("login")
        calls_after_build = backend.calls

        backend2 = _FakeBackend()
        ret2 = NeuralSemanticRetriever(FILES, backend=backend2, cache_path=cache)
        self.assertEqual(backend2.calls, 0)      # document vectors from cache
        self.assertEqual(ret2.search("login"), first)

    def test_cache_invalidated_when_content_changes(self):
        cache = Path(self._tmp.name) / "emb.json"
        NeuralSemanticRetriever(FILES, backend=_FakeBackend(), cache_path=cache)
        changed = dict(FILES)
        changed["calc.py"] = "def divide(a, b):\n    return a / b\n"
        backend = _FakeBackend()
        ret = NeuralSemanticRetriever(changed, backend=backend, cache_path=cache)
        self.assertGreater(backend.calls, 0)     # stale cache → re-embed
        raw = json.loads(cache.read_text())
        self.assertEqual(raw["backend"], "fake(2d)")
        self.assertEqual(raw["paths"], sorted(changed))

    def test_lsa_backend_matches_legacy_ranking(self):
        from mini_claude.retrieval import SemanticRetriever
        backend = LSABackend()
        neural = NeuralSemanticRetriever(FILES, backend=backend)
        legacy = SemanticRetriever(FILES)
        self.assertEqual([h.file_path for h in neural.search("login password", top_k=3)],
                         [h.file_path for h in legacy.search("login password", top_k=3)])
        self.assertIn("lsa", neural.label)       # honestly labeled, never neural


class TestBackendDetection(unittest.TestCase):
    def test_prefer_lsa_always_wins(self):
        backend, note = detect_embedding_backend(prefer="lsa")
        self.assertIsInstance(backend, LSABackend)
        self.assertIn("lsa", note)

    def test_prefer_local_returns_fastembed_when_installed(self):
        try:
            import fastembed  # noqa: F401
        except ImportError:
            self.skipTest("fastembed not installed in this environment")
        from mini_claude.retrieval.embedding import FastembedBackend
        backend, note = detect_embedding_backend(prefer="local")
        self.assertIsInstance(backend, FastembedBackend)
        self.assertIn("fastembed", note)

    def test_prefer_api_without_config_falls_back_honestly(self):
        saved = {k: os.environ.pop(k) for k in ("EMBEDDING_API_URL",) if k in os.environ}
        try:
            backend, note = detect_embedding_backend(prefer="api")
            self.assertIsInstance(backend, LSABackend)
            self.assertIn("fallback", note)
        finally:
            os.environ.update(saved)

    def test_auto_prefers_configured_api(self):
        saved = {k: os.environ.pop(k) for k in ("EMBEDDING_API_URL",
                                                "EMBEDDING_API_KEY",
                                                "EMBEDDING_MODEL")
                 if k in os.environ}
        os.environ["EMBEDDING_API_URL"] = "https://emb.example/v1"
        os.environ["EMBEDDING_API_KEY"] = "sk-test"
        try:
            backend, note = detect_embedding_backend(prefer="auto")
            self.assertIsInstance(backend, APIEmbeddingBackend)
            self.assertEqual(backend.model, "text-embedding-3-small")
            self.assertIn("configured", note)
        finally:
            os.environ.update(saved)


class TestAPIEmbeddingBackend(unittest.TestCase):
    def test_request_shape_and_normalization(self):
        import urllib.request

        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({
                    "data": [{"index": 0, "embedding": [3.0, 4.0]},
                             {"index": 1, "embedding": [0.0, 5.0]}],
                }).encode()

        def fake_urlopen(req, timeout=60):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            captured["auth"] = req.headers.get("Authorization")
            return _Resp()

        saved = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            backend = APIEmbeddingBackend("https://emb.example/v1", "sk-test",
                                          "m-embed")
            vecs = backend.embed(["a", "b"])
        finally:
            urllib.request.urlopen = saved

        self.assertEqual(captured["url"], "https://emb.example/v1/embeddings")
        self.assertEqual(captured["body"], {"model": "m-embed", "input": ["a", "b"]})
        self.assertEqual(captured["auth"], "Bearer sk-test")
        # unit-normalized
        self.assertAlmostEqual(sum(x * x for x in vecs[0]), 1.0, places=6)
        self.assertAlmostEqual(vecs[1][1], 1.0, places=6)

    def test_http_error_is_explicit(self):
        import urllib.error
        import urllib.request

        def fake_urlopen(req, timeout=60):
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)

        saved = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(RuntimeError) as ctx:
                APIEmbeddingBackend("https://emb.example/v1", "k", "m").embed(["x"])
            self.assertIn("404", str(ctx.exception))
        finally:
            urllib.request.urlopen = saved


class TestHybridWiring(unittest.TestCase):
    def test_semantic_label_and_fusion_with_fake_backend(self):
        index = _FakeIndex()
        retriever = HybridRetriever(
            index, files=FILES, enable_lexical=True, enable_semantic=True,
            enable_structural=False, rerank=False,
            semantic_backend=_FakeBackend())
        self.assertEqual(retriever.semantic_label, "fake(2d)")
        hits = retriever.retrieve("login", top_k=5)
        self.assertTrue(hits)                     # fusion works end to end
        self.assertIn("auth.py", [h.file_path for h in hits])


class _FakeIndex:
    root = Path("/nonexistent")
    files = lambda self: list(FILES)
    find_symbol = lambda self, *a, **k: []
    symbols_in_file = lambda self, *a, **k: []
    file_dependencies = lambda self, *a, **k: []
    dependents = lambda self, *a, **k: []
    module_dependencies = lambda self, *a, **k: []
    module_of_file = lambda self, *a, **k: None


if __name__ == "__main__":
    unittest.main(verbosity=2)

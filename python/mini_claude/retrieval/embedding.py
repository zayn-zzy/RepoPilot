"""Neural embedding backends for semantic retrieval.

Three backends behind one interface (embed texts → unit-normalized
vectors), plus an honest auto-detector:

- FastembedBackend — a real neural model (BAAI/bge-small-en-v1.5 ONNX by
  default) running locally; weights download once from the HF Hub on
  first use and are cached on disk.
- APIEmbeddingBackend — any OpenAI-compatible /embeddings endpoint
  (EMBEDDING_API_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL). DeepSeek
  does NOT offer embeddings (probed: /embeddings 401 with a key, then
  404 for every model name) — recorded, not worked around.
- LSABackend — the original TF-IDF+SVD LSA as the deterministic
  dependency-light fallback; the label always says it is LSA, never
  "neural".

detect_embedding_backend() picks api → fastembed → LSA and always
returns the reason, so every retrieval result can say what produced it.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Protocol

from sklearn.decomposition import TruncatedSVD  # noqa: F401  (LSABackend)
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401


class EmbeddingBackend(Protocol):
    label: str        # what the result vectors really are (shown to users)
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Length must match the input length."""
        ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


# ─── FastembedBackend (local neural model) ───────────────────

class FastembedBackend:
    """Local ONNX neural embeddings. The model downloads on first embed()
    (HF Hub, cached under HF_HOME); construction itself is cheap and
    offline-safe."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5",
                 batch_size: int = 64):
        self.model_name = model_name
        self.batch_size = batch_size
        self.label = f"neural(fastembed:{model_name})"
        self._model = None
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._load()
        return self._dim

    def _load(self):
        from fastembed import TextEmbedding
        self._model = TextEmbedding(self.model_name)
        first = next(self._model.embed(["warmup"]))
        self._dim = int(first.shape[0])

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            self._load()
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            for vec in self._model.embed(batch):
                v = [float(x) for x in vec]
                out.append(_normalize(v))
        return out


# ─── APIEmbeddingBackend (OpenAI-compatible /embeddings) ─────

class APIEmbeddingBackend:
    """OpenAI-compatible embeddings endpoint (urllib — no SDK), with the
    net.py proxy-removed window for broken proxies and explicit errors."""

    def __init__(self, base_url: str, api_key: str, model: str,
                 batch_size: int = 64):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self.label = f"neural(api:{model})"
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed(["warmup"])[0])
        return self._dim

    def _request(self, payload: dict) -> dict:
        import urllib.error
        import urllib.request

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/embeddings", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with _proxy_free():
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(
                f"embedding API {self.base_url} returned {e.code}: {detail}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"embedding API {self.base_url} unreachable: {e} — "
                f"proxy state: {_proxy_state()}"
            ) from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            data = self._request({
                "model": self.model,
                "input": texts[i:i + self.batch_size],
            })
            rows = data.get("data") or []
            for row in sorted(rows, key=lambda r: r.get("index", 0)):
                out.append(_normalize([float(x) for x in row["embedding"]]))
        return out


# ─── LSABackend (the original LSA, honest fallback) ──────────

class LSABackend:
    """TF-IDF + truncated SVD — deterministic and dependency-light, and
    the label says exactly that. This is what semantic retrieval fell
    back to before neural embeddings were wired in."""

    def __init__(self, n_components: int = 100, random_state: int = 42):
        from .chinese import expand_cjk_bigrams
        self.label = f"lsa({n_components}d)"
        self._n = n_components
        self._state = random_state
        # CJK runs are expanded to space-separated bigrams BEFORE the
        # token pattern runs — the pattern accepts those 1-2 char words
        # (an ASCII-only pattern would drop every Chinese token again).
        # A custom preprocessor REPLACES sklearn's default lowercase
        # step, so lowercasing happens inside it (lowercase=False here).
        self._vec = TfidfVectorizer(
            preprocessor=lambda t: expand_cjk_bigrams(t).lower(),
            token_pattern=r"(?:[a-z_][a-z0-9_]*|[一-鿿]{1,2})",
            lowercase=False, sublinear_tf=True)
        self._svd: TruncatedSVD | None = None
        self._dim: int | None = None
        self._fitted_on: tuple[str, ...] = ()

    @property
    def dim(self) -> int:
        return self._dim or self._n

    def fit(self, corpus: list[str]) -> "LSABackend":
        """LSA is corpus-relative: fit once on the indexed texts, then
        embed queries into the same latent space (the vectorizer's
        preprocessor expands CJK runs to bigrams)."""
        self._fitted_on = tuple(corpus)
        if corpus:
            tfidf = self._vec.fit_transform(corpus)
            k = min(self._n, max(1, len(corpus) - 1))
            self._svd = TruncatedSVD(n_components=k, random_state=self._state)
            reduced = self._svd.fit_transform(tfidf)
            self._dim = k
            self._cache = [list(row) for row in reduced]
        else:
            self._svd = None
            self._dim = None
            self._cache = []
        return self

    def cached_document_vectors(self) -> list[list[float]] | None:
        """Vectors for the fit corpus (the index saves these)."""
        return list(self._cache) if hasattr(self, "_cache") else None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._svd is None:
            return []
        reduced = self._svd.transform(self._vec.transform(texts))
        return [list(row) for row in reduced]


# ─── detection ───────────────────────────────────────────────

def detect_embedding_backend(prefer: str | None = None,
                             files_for_lsa: list[str] | None = None,
                             ) -> tuple[EmbeddingBackend, str]:
    """Pick a backend and say why (the note goes into result labels).

    ``prefer``: "api" | "local" | "lsa" | None (auto = api → local → lsa).
    ``files_for_lsa``: corpus to fit LSA on when it is the chosen backend
    (the retriever fits it, so detection just returns the object)."""
    choice = prefer or "auto"

    if choice in ("auto", "api"):
        url = os.environ.get("EMBEDDING_API_URL")
        key = (os.environ.get("EMBEDDING_API_KEY")
               or os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        model = os.environ.get("EMBEDDING_MODEL") or "text-embedding-3-small"
        if url and key:
            return APIEmbeddingBackend(url, key, model), \
                f"api embedding configured ({url})"
        if choice == "api":
            return LSABackend(), ("api embedding requested but "
                                  "EMBEDDING_API_URL/key not set — LSA fallback")

    if choice in ("auto", "local"):
        try:
            import fastembed  # noqa: F401
            return FastembedBackend(), "local neural model (fastembed)"
        except ImportError:
            if choice == "local":
                return LSABackend(), "fastembed not installed — LSA fallback"

    return LSABackend(), ("lsa (TF-IDF+SVD) — deterministic fallback; "
                          "not a neural model")


# ─── helpers ─────────────────────────────────────────────────

def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    if not norm:
        return v
    return [x / norm for x in v]


def _proxy_state() -> str:
    found = [k for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
             if os.environ.get(k)]
    return f"proxies set: {found}" if found else "no proxy variables set"


class _proxy_free:
    """Reuse mini_claude.net's proxy-removed window when available (the
    vendored httpx bakes proxies at construction; urllib reads env at
    request time, so removal around the request suffices here)."""

    def __enter__(self):
        self._saved = {}
        self._vars = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                      "ALL_PROXY", "all_proxy")
        for k in self._vars:
            if k in os.environ:
                self._saved[k] = os.environ.pop(k)
        return self

    def __exit__(self, *exc):
        os.environ.update(self._saved)
        return False

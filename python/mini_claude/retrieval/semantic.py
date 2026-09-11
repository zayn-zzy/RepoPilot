"""Semantic retrieval — latent semantic indexing (TF-IDF + truncated SVD,
i.e. LSA) over file contents, ranked by cosine similarity. Deterministic
(random_state pinned) and dependency-light; the class shape (index once,
search many, cosine over vectors) matches a neural embedder, which can be
dropped in behind the same interface later."""

from __future__ import annotations

import re
from pathlib import Path

from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .embedding import LSABackend, _cosine, detect_embedding_backend
from .model import RetrievalHit


class SemanticRetriever:
    def __init__(self, files: dict[str, str], n_components: int = 100, random_state: int = 42):
        self.files = files
        self._paths = sorted(files)
        self._vec = TfidfVectorizer(
            token_pattern=r"[A-Za-z_][A-Za-z0-9_]+",
            lowercase=True,
            sublinear_tf=True,
        )
        if not self._paths:
            self._matrix = None
            self._svd = None
            return
        tfidf = self._vec.fit_transform([files[p] for p in self._paths])
        k = min(n_components, max(1, len(self._paths) - 1))
        self._svd = TruncatedSVD(n_components=k, random_state=random_state)
        self._matrix = self._svd.fit_transform(tfidf)

    def search(self, query, top_k: int = 10) -> list[RetrievalHit]:
        text = query.raw if hasattr(query, "raw") else str(query)
        if self._matrix is None:
            return []
        qvec = self._svd.transform(self._vec.transform([text]))
        sims = cosine_similarity(qvec, self._matrix)[0]
        ranked = sorted(
            zip(self._paths, sims), key=lambda p: p[1], reverse=True
        )
        return [
            RetrievalHit(path, float(score), sources={"semantic": float(score)})
            for path, score in ranked[:top_k]
            if score > 0
        ]


class NeuralSemanticRetriever:
    """Semantic retrieval over a pluggable embedding backend — a real
    neural model (fastembed/API) or the honest LSA fallback, decided by
    detect_embedding_backend(). Same shape as SemanticRetriever (index
    once, search many, cosine over vectors).

    Document vectors are cached to disk (JSON, keyed by backend label +
    content hash) so repeated builds don't re-embed the whole repository;
    a changed file or backend invalidates the cache."""

    def __init__(self, files: dict[str, str], backend=None, *,
                 cache_path=None):
        self.files = files
        self._cosine = _cosine
        self._paths = sorted(files)
        self.cache_path = Path(cache_path) if cache_path else None
        if backend is not None:
            self.backend = backend
            self.note = "explicitly configured backend"
        else:
            self.backend, self.note = detect_embedding_backend()
        self._matrix: dict[str, list[float]] = {}
        self._build()

    @property
    def label(self) -> str:
        return self.backend.label

    # ─── build + cache ───────────────────────────────────────

    def _content_key(self) -> str:
        import hashlib
        blob = "\0".join(f"{p}\0{c}" for p, c in sorted(self.files.items()))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _build(self) -> None:
        if not self._paths:
            self._matrix = {}
            return
        if self.cache_path is not None and self._load_cache():
            return
        corpus = [self.files[p] for p in self._paths]
        if isinstance(self.backend, LSABackend):
            self.backend.fit(corpus)
            vecs = self.backend.cached_document_vectors() or []
        else:
            vecs = self.backend.embed(corpus)
        if len(vecs) != len(self._paths):
            raise RuntimeError(
                f"embedding backend returned {len(vecs)} vectors for "
                f"{len(self._paths)} documents")
        self._matrix = dict(zip(self._paths, vecs))
        if self.cache_path is not None:
            self._save_cache()

    def _save_cache(self) -> None:
        import json
        data = {
            "backend": self.backend.label,
            "content_key": self._content_key(),
            "dim": len(next(iter(self._matrix.values()), [])),
            "paths": self._paths,
            "vectors": {p: v for p, v in self._matrix.items()},
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=1))

    def _load_cache(self) -> bool:
        import json
        if not self.cache_path.is_file():
            return False
        try:
            data = json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if (data.get("backend") != self.backend.label
                or data.get("content_key") != self._content_key()
                or data.get("paths") != self._paths):
            return False  # stale: content or backend changed — rebuild
        self._matrix = dict(data["vectors"])
        return bool(self._matrix) or not self._paths

    # ─── search ──────────────────────────────────────────────

    def search(self, query, top_k: int = 10) -> list[RetrievalHit]:
        text = query.raw if hasattr(query, "raw") else str(query)
        if not self._matrix:
            return []
        qvecs = self.backend.embed([text])
        if not qvecs:
            return []
        qvec = qvecs[0]
        sims = [(path, self._cosine(qvec, vec))
                for path, vec in self._matrix.items()]
        ranked = sorted(sims, key=lambda p: p[1], reverse=True)
        return [
            RetrievalHit(path, float(score),
                         sources={"semantic": float(score),
                                  "backend": self.backend.label})
            for path, score in ranked[:top_k]
            if score > 0
        ]

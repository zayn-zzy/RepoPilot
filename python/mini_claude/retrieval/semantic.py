"""Semantic retrieval — latent semantic indexing (TF-IDF + truncated SVD,
i.e. LSA) over file contents, ranked by cosine similarity. Deterministic
(random_state pinned) and dependency-light; the class shape (index once,
search many, cosine over vectors) matches a neural embedder, which can be
dropped in behind the same interface later."""

from __future__ import annotations

import re

from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

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

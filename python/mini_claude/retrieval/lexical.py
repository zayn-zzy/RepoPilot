"""Lexical retrieval — BM25 over tokenized file contents, with an optional
symbol-name field (from the repository index) weighted higher so identifier
queries surface the defining file."""

from __future__ import annotations

import math
import re
from collections import Counter

from .model import RetrievalHit

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

K1 = 1.5
B = 0.75
SYMBOL_FIELD_BOOST = 2.0


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class LexicalRetriever:
    def __init__(self, files: dict[str, str], symbol_index=None):
        """`files` maps repo-relative path -> content. `symbol_index` is an
        optional RepositoryIndex; its symbol names become a boosted field."""
        self.files = files
        self.symbol_index = symbol_index
        self._doc_tokens: dict[str, Counter] = {}
        self._doc_len: dict[str, int] = {}
        self._df: Counter = Counter()      # term -> documents containing it
        self._avg_len = 0.0
        self._build_index()

    def _build_index(self) -> None:
        for path, content in self.files.items():
            counter = Counter(tokenize(content))
            if symbol_index := self.symbol_index:
                for sym in symbol_index.symbols_in_file(path):
                    # Qualified names split on '.' only — snake_case names
                    # (check_permission) must stay whole, they ARE the token.
                    for part in sym.qualified_name.split("."):
                        if part and part != path.split("/")[-1][:-3]:
                            counter[part.lower()] += SYMBOL_FIELD_BOOST
            self._doc_tokens[path] = counter
            self._doc_len[path] = sum(counter.values())
            for term in counter:
                self._df[term] += 1
        n = len(self._doc_tokens)
        self._avg_len = sum(self._doc_len.values()) / n if n else 0.0
        # idf for every seen term
        self._idf: dict[str, float] = {
            t: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for t, df in self._df.items()
        }

    def _bm25(self, path: str, query_terms: list[str]) -> float:
        counter = self._doc_tokens[path]
        doc_len = self._doc_len[path]
        denom_factor = 1 - B + B * (doc_len / self._avg_len if self._avg_len else 1)
        score = 0.0
        for term in dict.fromkeys(query_terms):
            tf = counter.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf.get(term, 0.0)
            score += idf * (tf * (K1 + 1)) / (tf + K1 * denom_factor)
        return score

    def search(self, query, top_k: int = 10) -> list[RetrievalHit]:
        """`query` is an AnalyzedQuery or a plain string (tokenized as-is)."""
        if hasattr(query, "terms"):
            terms = query.terms
        else:
            terms = tokenize(query)
        if not terms:
            return []
        scored = [
            RetrievalHit(path, score, sources={"lexical": score})
            for path in self._doc_tokens
            if (score := self._bm25(path, terms)) > 0
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

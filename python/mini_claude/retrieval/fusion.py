"""Candidate merge — reciprocal rank fusion (RRF) and weighted score
fusion — plus a deterministic content-based Reranker."""

from __future__ import annotations

from collections import Counter, defaultdict

from .model import RetrievalHit

RRF_K = 60


def reciprocal_rank_fusion(
    hit_lists: dict[str, list[RetrievalHit]],
    weights: dict[str, float] | None = None,
    k: int = RRF_K,
    top_k: int | None = None,
) -> list[RetrievalHit]:
    """Rank-based fusion robust to per-retriever score scales: each hit
    contributes w_r / (k + rank_r)."""
    weights = weights or {}
    fused: dict[str, RetrievalHit] = {}
    for source, hits in hit_lists.items():
        w = weights.get(source, 1.0)
        for rank, hit in enumerate(hits):
            rrf = w / (k + rank + 1)
            if hit.file_path not in fused:
                fused[hit.file_path] = RetrievalHit(
                    hit.file_path, rrf, sources={source: hit.score}, symbols=list(hit.symbols),
                )
            else:
                out = fused[hit.file_path]
                out.score += rrf
                out.sources[source] = hit.score
                for s in hit.symbols:
                    if s not in out.symbols:
                        out.symbols.append(s)
    ranked = sorted(fused.values(), key=lambda h: h.score, reverse=True)
    return ranked[:top_k] if top_k else ranked


def weighted_fusion(
    hit_lists: dict[str, list[RetrievalHit]],
    weights: dict[str, float] | None = None,
    top_k: int | None = None,
) -> list[RetrievalHit]:
    """Score-based fusion: each retriever's scores are min-max normalized
    before the weighted sum, so scale differences don't dominate."""
    weights = weights or {}
    fused: dict[str, RetrievalHit] = {}
    for source, hits in hit_lists.items():
        w = weights.get(source, 1.0)
        if not hits:
            continue
        lo = min(h.score for h in hits)
        hi = max(h.score for h in hits)
        span = (hi - lo) or 1.0
        for hit in hits:
            norm = (hit.score - lo) / span
            if hit.file_path not in fused:
                fused[hit.file_path] = RetrievalHit(
                    hit.file_path, w * norm, sources={source: norm}, symbols=list(hit.symbols),
                )
            else:
                out = fused[hit.file_path]
                out.score += w * norm
                out.sources[source] = norm
                for s in hit.symbols:
                    if s not in out.symbols:
                        out.symbols.append(s)
    ranked = sorted(fused.values(), key=lambda h: h.score, reverse=True)
    return ranked[:top_k] if top_k else ranked


class Reranker:
    """Deterministic rerank of fused candidates. Evidence comes from the
    per-retriever raw scores preserved in each hit's `sources` (BM25 is
    IDF-weighted, LSA cosine is semantic) — normalized per candidate set so
    the scales are comparable — plus symbol-name and path bonuses and a small
    fusion-agreement term. Raw term-density counting was deliberately
    replaced: unweighted occurrences of common tokens drowned the signal."""

    def __init__(self, files: dict[str, str], index=None):
        self.files = files
        self.index = index

    def _symbol_bonus(self, path: str, hints: list[str]) -> float:
        if self.index is None:
            return 0.0
        bonus = 0.0
        for sym in self.index.symbols_in_file(path):
            for hint in hints:
                if sym.name == hint:
                    bonus += 2.0
                elif hint in sym.name:
                    bonus += 0.5
        return bonus

    def _path_bonus(self, path: str, path_hints: list[str]) -> float:
        # An explicitly named file must outrank incidental term matches.
        return 5.0 * sum(1 for h in path_hints if path.endswith(h) or h in path)

    @staticmethod
    def _normalizer(candidates: list[RetrievalHit], source: str) -> float:
        return max((h.sources.get(source, 0.0) for h in candidates), default=0.0) or 1.0

    def rerank(self, query, candidates: list[RetrievalHit], top_k: int = 10) -> list[RetrievalHit]:
        hints = getattr(query, "symbol_hints", []) or []
        path_hints = getattr(query, "path_hints", []) or []

        lex_max = self._normalizer(candidates, "lexical")
        sem_max = self._normalizer(candidates, "semantic")
        str_max = self._normalizer(candidates, "structural")
        fused_max = max((h.score for h in candidates), default=0.0) or 1.0

        for hit in candidates:
            lex = hit.sources.get("lexical", 0.0) / lex_max
            sem = hit.sources.get("semantic", 0.0) / sem_max
            str_ = hit.sources.get("structural", 0.0) / str_max
            hit.score = (
                lex
                + 0.5 * sem
                + 0.2 * str_                # graph evidence, modest weight
                + self._symbol_bonus(hit.file_path, hints)
                + self._path_bonus(hit.file_path, path_hints)
                + 0.3 * (hit.score / fused_max)   # fusion agreement
            )
        candidates.sort(key=lambda h: h.score, reverse=True)
        return candidates[:top_k]

"""HybridRetriever — the end-to-end pipeline:

    Requirement → QueryAnalyzer → {Lexical, Semantic, Structural}
                  → Candidate Merge (RRF) → Reranker → Context Builder

Retrievers can be toggled individually (the benchmark's ablations construct
Grep / Semantic / Hybrid / Hybrid+Graph from the same building blocks)."""

from __future__ import annotations

import re
from pathlib import Path

from .analyzer import QueryAnalyzer
from .context import ContextBuilder
from .fusion import Reranker, reciprocal_rank_fusion
from .lexical import LexicalRetriever
from .model import RetrievalHit
from .semantic import NeuralSemanticRetriever
from .structural import StructuralRetriever

DEFAULT_WEIGHTS = {"lexical": 1.0, "semantic": 1.0, "structural": 0.6}


def grep_baseline(files: dict[str, str], query: str, top_k: int = 10) -> list[RetrievalHit]:
    """Plain regex grep ranked by total match count — the benchmark's
    Baseline A. Every literal query token is OR-ed as a regex; Chinese
    queries contribute their CJK spans as literal terms (a pure-Chinese
    query used to match nothing)."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query)
    from .chinese import cjk_spans
    tokens += cjk_spans(query)
    if not tokens:
        return []
    pattern = re.compile("|".join(re.escape(t) for t in tokens), re.IGNORECASE)
    scored: list[RetrievalHit] = []
    for path, content in files.items():
        count = len(pattern.findall(content))
        if count:
            scored.append(RetrievalHit(path, float(count), sources={"grep": float(count)}))
    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:top_k]


class HybridRetriever:
    def __init__(
        self,
        index,
        *,
        files: dict[str, str] | None = None,
        enable_lexical: bool = True,
        enable_semantic: bool = True,
        enable_structural: bool = True,
        max_hop: int = 2,
        weights: dict[str, float] | None = None,
        rerank: bool = True,
        # Phase 14: the semantic stack's embedding backend (auto-detect
        # by default: configured API → local fastembed → honest LSA).
        semantic_backend=None,
        semantic_cache=None,
    ):
        self.index = index
        self.files = files if files is not None else self._read_files(index)
        self.weights = weights or DEFAULT_WEIGHTS
        self.max_hop = max_hop
        self.rerank_enabled = rerank
        self.analyzer = QueryAnalyzer()
        self.lexical = LexicalRetriever(self.files, symbol_index=index) if enable_lexical else None
        self.semantic = (NeuralSemanticRetriever(
            self.files, backend=semantic_backend, cache_path=semantic_cache)
            if enable_semantic else None)
        self.structural = StructuralRetriever(index) if enable_structural else None
        self.reranker = Reranker(self.files, index) if rerank else None
        self.context_builder = ContextBuilder(self.files, index)

    @property
    def semantic_label(self) -> str:
        """What produced the semantic scores (neural model / LSA) —
        surfaced to callers so results never pretend."""
        return self.semantic.label if self.semantic is not None else "off"

    @staticmethod
    def _read_files(index) -> dict[str, str]:
        root = index.root
        files: dict[str, str] = {}
        for rel in index.files():
            try:
                files[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        return files

    # ─── Retrieval ───────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalHit]:
        analyzed = self.analyzer.analyze(query)
        hit_lists: dict[str, list[RetrievalHit]] = {}
        if self.lexical is not None:
            hit_lists["lexical"] = self.lexical.search(analyzed, top_k=max(top_k * 2, 20))
        if self.semantic is not None:
            hit_lists["semantic"] = self.semantic.search(analyzed, top_k=max(top_k * 2, 20))
        if self.structural is not None:
            hit_lists["structural"] = self.structural.search(analyzed, top_k=max(top_k * 2, 20), max_hop=self.max_hop)
        # Explicit file/module targets in the query short-circuit to exact
        # path hits — "read pkg/utils.py" must surface pkg/utils.py even when
        # its content never mentions its own name.
        if analyzed.path_hints:
            hit_lists["path"] = [
                RetrievalHit(p, 5.0, sources={"path": 5.0})
                for p in self.files
                if any(p.endswith(h) or h in p for h in analyzed.path_hints)
            ]

        fused = reciprocal_rank_fusion(hit_lists, weights=self.weights)
        if self.reranker is not None:
            fused = self.reranker.rerank(analyzed, fused, top_k=top_k)
        else:
            fused = fused[:top_k]
        return fused

    # ─── Context ─────────────────────────────────────────────

    def build_context(self, query: str, token_budget: int, top_k: int = 10) -> str:
        hits = self.retrieve(query, top_k=top_k)
        return self.context_builder.build_context(hits, token_budget)

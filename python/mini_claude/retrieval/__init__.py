"""RepoPilot Hybrid Retrieval — lexical (BM25), semantic (LSA) and
structural (symbol + dependency-graph) retrieval with RRF fusion, reranking,
and a token-aware context builder. Built on the Phase 2 RepositoryIndex."""

from .analyzer import AnalyzedQuery, QueryAnalyzer
from .context import ContextBuilder, estimate_tokens
from .fusion import Reranker, reciprocal_rank_fusion, weighted_fusion
from .lexical import LexicalRetriever
from .model import RetrievalHit
from .pipeline import HybridRetriever, grep_baseline
from .embedding import detect_embedding_backend
from .semantic import NeuralSemanticRetriever, SemanticRetriever
from .structural import StructuralRetriever

__all__ = [
    "AnalyzedQuery",
    "ContextBuilder",
    "HybridRetriever",
    "LexicalRetriever",
    "QueryAnalyzer",
    "Reranker",
    "RetrievalHit",
    "SemanticRetriever",
    "NeuralSemanticRetriever",
    "detect_embedding_backend",
    "StructuralRetriever",
    "estimate_tokens",
    "grep_baseline",
    "reciprocal_rank_fusion",
    "weighted_fusion",
]

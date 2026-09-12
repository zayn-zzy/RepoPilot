"""AskService — the `repopilot ask` use case as a callable service.

Structured retrieval evidence (per-hit source scores, honestly labeled
backend) plus the optional LLM answer. The CLI formats this to the
terminal; the Web layer will serialize the same object to JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .repository import _repopilot_dir


@dataclass
class AskHit:
    """One fused retrieval hit with its honest per-source scores."""
    file_path: str
    score: float
    sources: dict = field(default_factory=dict)


@dataclass
class AskResult:
    question: str
    index_note: str
    semantic_label: str
    semantic_note: str
    context: str
    hits: list[AskHit] = field(default_factory=list)
    answer: str = ""
    has_answer: bool = False


class AskService:
    def ask(self, path: str | Path, question: str, *,
            model: str = "deepseek-v4-pro[1m]",
            semantic: str = "auto",
            api_key: str | None = None,
            base_url: str | None = None,
            answer: bool = True,
            top_k: int = 5,
            token_budget: int = 3000,
            ) -> AskResult:
        from ..retrieval import HybridRetriever, detect_embedding_backend
        from ..repo import RepositoryIndex
        root = Path(path).resolve()
        index = RepositoryIndex(root)
        _, index_note = index.load_or_build(_repopilot_dir(root) / "index.db")
        backend, note = detect_embedding_backend(prefer=semantic)
        cache = _repopilot_dir(root) / "embeddings-cache.json"
        retriever = HybridRetriever(index, semantic_backend=backend,
                                    semantic_cache=cache)
        context = retriever.build_context(question, token_budget=token_budget,
                                          top_k=top_k)
        hits = [AskHit(file_path=h.file_path, score=h.score,
                       sources=dict(h.sources))
                for h in retriever.retrieve(question, top_k=top_k)]
        result = AskResult(
            question=question, index_note=index_note,
            semantic_label=retriever.semantic_label, semantic_note=note,
            context=context, hits=hits)
        if not answer or not api_key:
            return result
        from ..runtime import AgentRuntime, AgentConfig, build_default_registry
        runtime = AgentRuntime(AgentConfig(
            role="general", model=model, api_key=api_key,
            anthropic_base_url=base_url,
            custom_system_prompt="You answer questions about a code repository "
                                 "using the provided context.",
        ), registry=build_default_registry())
        import asyncio
        response = asyncio.run(runtime.run(
            f"Context from the repository:\n{context}\n\nQuestion: {question}\n"
            "Answer concisely, citing file paths."))
        result.answer = response.text
        result.has_answer = True
        return result

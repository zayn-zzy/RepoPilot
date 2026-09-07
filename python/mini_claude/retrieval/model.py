"""Shared types for the retrieval pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievalHit:
    """One retrieved file with its fused score and provenance."""

    file_path: str
    score: float
    sources: dict[str, float] = field(default_factory=dict)  # retriever -> its normalized score
    symbols: list[str] = field(default_factory=list)         # matched symbol qualified names

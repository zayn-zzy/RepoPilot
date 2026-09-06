"""LLMProvider — backend selection and model capability metadata.

The transport itself (streaming, retries, caching) lives in the original Agent
loop and is deliberately reused. This provider is the config/metadata layer:
which backend, which model, and what that model can do — the knobs the
original loop consulted via module-level functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..agent import (
    _get_context_window,
    _get_max_output_tokens,
    _model_supports_adaptive_thinking,
    _model_supports_thinking,
)

if TYPE_CHECKING:  # pragma: no cover
    from .config import AgentConfig

BACKENDS = ("anthropic", "openai")


@dataclass(frozen=True)
class LLMProvider:
    """Describes one LLM endpoint. `api_base` is the OpenAI-compatible base URL
    when backend == "openai", or the Anthropic base_url override otherwise."""

    backend: str
    model: str
    api_key: str | None = None
    api_base: str | None = None

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError(f"unknown backend {self.backend!r} (expected one of {BACKENDS})")

    @property
    def use_openai(self) -> bool:
        return self.backend == "openai"

    @property
    def context_window(self) -> int:
        return _get_context_window(self.model)

    @property
    def max_output_tokens(self) -> int:
        return _get_max_output_tokens(self.model)

    @property
    def supports_thinking(self) -> bool:
        return _model_supports_thinking(self.model)

    @property
    def supports_adaptive_thinking(self) -> bool:
        return _model_supports_adaptive_thinking(self.model)

    @classmethod
    def from_config(cls, config: "AgentConfig") -> "LLMProvider":
        """Map an AgentConfig onto the backend split the Agent constructor
        uses: api_base set → OpenAI-compatible, otherwise Anthropic."""
        if config.api_base:
            return cls("openai", config.model, config.api_key, config.api_base)
        return cls("anthropic", config.model, config.api_key, config.anthropic_base_url)

    def to_agent_kwargs(self) -> dict[str, Any]:
        """The subset of Agent.__init__ kwargs this provider covers."""
        if self.use_openai:
            return {
                "api_base": self.api_base,
                "anthropic_base_url": None,
                "api_key": self.api_key,
            }
        return {
            "api_base": None,
            "anthropic_base_url": self.api_base,
            "api_key": self.api_key,
        }

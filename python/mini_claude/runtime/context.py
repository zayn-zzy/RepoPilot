"""Context interface — a backend-agnostic view over the agent's conversation
history. The storage stays inside the original Agent (its dual
_anthropic_messages / _openai_messages lists); this is the stable interface
later phases build on instead of reaching into private fields."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from ..agent import Agent


class Context(Protocol):
    """What every context implementation must provide."""

    def message_count(self) -> int: ...

    def messages(self) -> list[dict]: ...

    def last_message(self) -> dict | None: ...

    def clear(self) -> None: ...


class AgentContext:
    """Adapts one Agent instance's message history."""

    def __init__(self, agent: "Agent"):
        self._agent = agent

    @property
    def backend(self) -> str:
        return "openai" if self._agent.use_openai else "anthropic"

    def message_count(self) -> int:
        return self._agent._get_message_count()

    def messages(self) -> list[dict]:
        """A copy of the current backend's message list."""
        if self._agent.use_openai:
            return list(self._agent._openai_messages)
        return list(self._agent._anthropic_messages)

    def last_message(self) -> dict | None:
        messages = self.messages()
        return messages[-1] if messages else None

    def clear(self) -> None:
        self._agent.clear_history()

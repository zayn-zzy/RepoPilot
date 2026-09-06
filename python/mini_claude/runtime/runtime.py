"""AgentRuntime — the reusable per-role runtime that drives the original
Agent loop.

```python
planner = AgentRuntime(AgentConfig.from_role("planner", model=...))
explorer = AgentRuntime(AgentConfig.from_role("explorer", model=...))
```

Each instance owns its ToolRegistry, ToolACL, Budget, Trace, and context view —
nothing is shared, so multiple roles can run independently. The Agent Loop
itself (streaming, compression, permission flow, plan mode, sub-agents) is
reused unchanged; the runtime wires the loop to the new seams via the hooks
added to Agent (`_event_emit`, `_tool_dispatcher`, `_active_tool_set`)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..agent import Agent
from .budget import Budget
from .config import AgentConfig
from .context import AgentContext
from .events import AgentEvents, EventEmitter
from .permissions import ToolACL
from .provider import LLMProvider
from .registry import ToolRegistry, build_default_registry
from .trace import Trace


@dataclass
class RunResult:
    """Outcome of one AgentRuntime.run() call."""

    text: str
    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    tokens_cache_creation: int
    turns: int
    cost_usd: float
    trace: Trace
    budget: Budget


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        *,
        registry: ToolRegistry | None = None,
        events: EventEmitter | None = None,
    ):
        config.validate()
        self.config = config
        self.events = events or EventEmitter()
        self.trace = Trace().attach(self.events)
        self.registry = registry if registry is not None else build_default_registry()
        self.acl = ToolACL(
            read_only=config.read_only,
            allowed_tools=config.tool_names,
            permission_mode=config.permission_mode,
        )
        self.budget = Budget(max_cost_usd=config.max_cost_usd, max_turns=config.max_turns)
        self.provider = LLMProvider.from_config(config)
        self._run_id = uuid.uuid4().hex[:8]
        self._agent = self._build_agent()
        self.context = AgentContext(self._agent)

    # ─── Agent construction ──────────────────────────────────

    def _build_agent(self) -> Agent:
        """Wire the original Agent to this runtime's seams. The loop stays
        unchanged; only its dispatch, deferred-activation state, and event
        hook point at the runtime."""
        agent = Agent(
            permission_mode=self.config.permission_mode,
            model=self.config.model,
            thinking=self.config.thinking,
            max_cost_usd=self.config.max_cost_usd,
            max_turns=self.config.max_turns,
            custom_system_prompt=self.config.custom_system_prompt,
            # Only ACL-visible tools are exposed to the model.
            custom_tools=self.acl.filter_definitions(self.registry.active_definitions()),
            is_sub_agent=not self.config.interactive,
            **self.provider.to_agent_kwargs(),
        )
        agent._event_emit = self.events.emit
        agent._tool_dispatcher = self._dispatch_tool
        agent._active_tool_set = self.registry._activated
        return agent

    async def _dispatch_tool(self, name: str, inp: dict, read_file_state: dict[str, float] | None) -> str:
        return await self.registry.dispatch(name, inp, acl=self.acl, read_file_state=read_file_state)

    # ─── Execution ───────────────────────────────────────────

    async def run(self, prompt: str) -> RunResult:
        """Run one prompt through the agent loop, capturing text, token
        deltas, budget updates, and the full event trace."""
        prev_in = self._agent.total_input_tokens
        prev_out = self._agent.total_output_tokens
        prev_cr = self._agent.total_cache_read_tokens
        prev_cc = self._agent.total_cache_creation_tokens

        self.events.emit(AgentEvents.RUN_STARTED, {
            "run_id": self._run_id,
            "role": self.config.role,
            "model": self.config.model,
            "backend": self.provider.backend,
        })

        result = await self._agent.run_once(prompt)

        tokens_in = self._agent.total_input_tokens - prev_in
        tokens_out = self._agent.total_output_tokens - prev_out
        cache_read = self._agent.total_cache_read_tokens - prev_cr
        cache_creation = self._agent.total_cache_creation_tokens - prev_cc
        self.budget.record_tokens(
            input=tokens_in, output=tokens_out,
            cache_read=cache_read, cache_creation=cache_creation,
        )

        self.events.emit(AgentEvents.RUN_FINISHED, {
            "run_id": self._run_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "turns": self._agent.current_turns,
            "cost_usd": self.budget.cost_usd,
        })

        # The stream emits a leading newline before the first text delta
        # (separates output from the spinner) — cosmetic for a terminal,
        # noise for a programmatic API.
        return RunResult(
            text=result["text"].strip(),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_cache_read=cache_read,
            tokens_cache_creation=cache_creation,
            turns=self._agent.current_turns,
            cost_usd=self.budget.cost_usd,
            trace=self.trace,
            budget=self.budget,
        )

    # ─── Control ─────────────────────────────────────────────

    @property
    def agent(self) -> Agent:
        """The underlying Agent instance (advanced use)."""
        return self._agent

    def abort(self) -> None:
        self._agent.abort()

    async def close(self) -> None:
        await self._agent.close()

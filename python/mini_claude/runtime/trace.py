"""Basic tracing — record the run's event stream with timestamps and compute
simple metrics. Data source for the Observability requirements (LLM calls,
tool calls, tokens, cost) and later benchmark phases."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .events import AgentEvents, EventEmitter


@dataclass
class TraceEvent:
    event_type: str
    data: dict
    ts_epoch: float
    ts_monotonic: float


class Trace:
    """An in-memory event log. Attach it to an EventEmitter and it records
    every typed event with wall-clock and monotonic timestamps."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def attach(self, emitter: EventEmitter) -> "Trace":
        for event_type in AgentEvents.ALL:
            emitter.on(event_type, self._recorder(event_type))
        return self

    def _recorder(self, event_type: str):
        def _record(data: dict) -> None:
            self.events.append(
                TraceEvent(
                    event_type=event_type,
                    data=dict(data),
                    ts_epoch=time.time(),
                    ts_monotonic=time.monotonic(),
                )
            )

        return _record

    def filter(self, event_type: str) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type == event_type]

    def metrics(self) -> dict[str, Any]:
        """Aggregate counters over the recorded run."""
        llm_requests = self.filter(AgentEvents.LLM_REQUEST)
        tool_calls = self.filter(AgentEvents.TOOL_CALL)
        finished = self.filter(AgentEvents.RUN_FINISHED)
        started = self.filter(AgentEvents.RUN_STARTED)
        runtime_s = None
        if started and finished:
            runtime_s = finished[-1].ts_monotonic - started[0].ts_monotonic
        final_data = finished[-1].data if finished else {}
        return {
            "llm_calls": len(llm_requests),
            "tool_calls": len(tool_calls),
            "tool_results": len(self.filter(AgentEvents.TOOL_RESULT)),
            "permission_denials": len(self.filter(AgentEvents.PERMISSION_DENIED)),
            "budget_exceeded": len(self.filter(AgentEvents.BUDGET_EXCEEDED)),
            "input_tokens": final_data.get("tokens_in", 0),
            "output_tokens": final_data.get("tokens_out", 0),
            "turns": final_data.get("turns", 0),
            "cost_usd": final_data.get("cost_usd", 0.0),
            "runtime_s": runtime_s,
        }

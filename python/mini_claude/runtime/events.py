"""Unified event system — typed agent events with dict payloads.

The original CLI agent calls ui.print_* directly, which couples the loop to a
terminal. The runtime instead emits typed events; the Trace subscribes to them,
and later phases (UI, benchmark, persistence) can subscribe without touching
the loop."""

from __future__ import annotations

from typing import Any, Callable

EventHandler = Callable[[dict], Any]


class AgentEvents:
    """Canonical event type names. The Agent loop emits these as plain strings
    (it must not import this package — the runtime owns the constant names)."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    LLM_REQUEST = "llm_request"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERMISSION_DENIED = "permission_denied"
    BUDGET_EXCEEDED = "budget_exceeded"

    ALL = (
        RUN_STARTED,
        RUN_FINISHED,
        LLM_REQUEST,
        TOOL_CALL,
        TOOL_RESULT,
        PERMISSION_DENIED,
        BUDGET_EXCEEDED,
    )


class EventEmitter:
    """Synchronous publish/subscribe over typed string events with dict payloads."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def on(self, event_type: str, handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def emit(self, event_type: str, data: dict | None = None) -> None:
        for handler in list(self._handlers.get(event_type, ())):
            try:
                handler(data or {})
            except Exception:
                # A listener error must never take down the agent loop.
                pass

    def listener_count(self, event_type: str | None = None) -> int:
        if event_type is not None:
            return len(self._handlers.get(event_type, ()))
        return sum(len(v) for v in self._handlers.values())

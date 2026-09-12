"""The unified Web event schema (§7) — one RunEvent shape for the
worker, the SSE stream, and the persisted event store. The fixed
event-type vocabulary is the contract the frontend renders from."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event_id() -> str:
    return uuid.uuid4().hex[:16]


class RunEvent(BaseModel):
    event_id: str = Field(default_factory=_event_id)
    run_id: str
    task_id: str | None = None
    agent_id: str | None = None
    event_type: str
    timestamp: datetime = Field(default_factory=_now)
    status: str | None = None
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# The fixed event vocabulary from §7 — validation and documentation.
EVENT_TYPES = frozenset("""
run.created run.queued run.started run.completed run.failed run.cancelled
plan.started plan.completed
task.ready task.started task.completed task.failed
agent.started agent.completed agent.failed
tool.started tool.completed tool.failed
file.changed
verification.started verification.passed verification.failed
repair.started repair.completed repair.failed
review.started review.approved review.rejected
approval.required approval.resolved
benchmark.started benchmark.completed
""".split())


def make_event(run_id: str, event_type: str, **fields) -> RunEvent:
    """A RunEvent with the housekeeping fields filled in."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown run event type: {event_type!r}")
    return RunEvent(run_id=run_id, event_type=event_type, **fields)

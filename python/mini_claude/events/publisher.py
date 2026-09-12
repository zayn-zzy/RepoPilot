"""EventPublisher — the single path events take to reach consumers:
persist to SQLite (the durable record) AND publish to the event bus
(real-time delivery). Consumed from the worker's event loop; a
thread-safe sink bridges the DagRunner's worker threads."""

from __future__ import annotations

import asyncio
from typing import Any

from .schema import RunEvent
from .store import EventStore


class EventPublisher:
    def __init__(self, store: EventStore, bus):
        self._store = store
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._drain_task: asyncio.Task | None = None

    async def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Begin draining the thread-safe sink (call once per loop)."""
        self._loop = loop or asyncio.get_running_loop()
        self._drain_task = asyncio.create_task(self._drain())

    async def stop(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass

    async def _drain(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, RunEvent):
                    await self.publish(item)
                else:  # dict from the thread sink
                    await self.publish(RunEvent(**item))
            except Exception as e:
                import logging
                logging.getLogger("repopilot.events").exception(
                    "event publish failed: %s", e)

    async def publish(self, event: RunEvent) -> None:
        """Durable + real-time: SQLite first, then the bus."""
        stored = self._store.append(event)
        await self._bus.publish_event(event.run_id, stored)

    def sink(self, run_id: str, event_type: str, *,
             task_id: str | None = None, agent_id: str | None = None,
             status: str | None = None, message: str | None = None,
             payload: dict[str, Any] | None = None) -> None:
        """Thread-safe entry point for synchronous code paths (the
        DagRunner's worker threads): the event is queued onto the loop
        that drains it. Never blocks a task thread on Redis."""
        event = RunEvent(run_id=run_id, event_type=event_type,
                         task_id=task_id, agent_id=agent_id,
                         status=status, message=message,
                         payload=payload or {})
        if self._loop is None or self._loop.is_closed():
            return  # publisher not started — events are dropped, not errors
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

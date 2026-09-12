"""CancelRegistry — the §45 cancellation flag shared between the API
process and the worker process (via Redis) or in-process (dev mode).

The worker polls the flag asynchronously and mirrors it into a local
asyncio.Event; the DagRunner's sync ``cancel_check`` just reads the
event — a cancel is never cosmetic: it stops tasks at boundaries."""

from __future__ import annotations

import asyncio


class CancelRegistry:
    async def request_cancel(self, run_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def sync(self, run_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def is_cancelled(self, run_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class InProcessCancelRegistry(CancelRegistry):
    """Single-process dev mode: API and worker share this object."""

    def __init__(self):
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, run_id: str) -> asyncio.Event:
        if run_id not in self._events:
            self._events[run_id] = asyncio.Event()
        return self._events[run_id]

    async def request_cancel(self, run_id: str) -> None:
        self._event(run_id).set()

    async def sync(self, run_id: str) -> None:
        pass  # same process — the event is already current

    def is_cancelled(self, run_id: str) -> bool:
        return self._event(run_id).is_set()


class RedisCancelRegistry(CancelRegistry):
    """Multi-process: the API sets a Redis flag; the worker polls it
    into its local event (0.5s cadence)."""

    def __init__(self, bus):
        self._bus = bus
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, run_id: str) -> asyncio.Event:
        if run_id not in self._events:
            self._events[run_id] = asyncio.Event()
        return self._events[run_id]

    async def request_cancel(self, run_id: str) -> None:
        r = await self._bus._redis()
        await r.set(f"repopilot:cancel:{run_id}", "1", ex=86_400)

    async def sync(self, run_id: str) -> None:
        r = await self._bus._redis()
        flag = await r.get(f"repopilot:cancel:{run_id}")
        if flag:
            self._event(run_id).set()

    def is_cancelled(self, run_id: str) -> bool:
        return self._event(run_id).is_set()


def build_cancel_registry(bus) -> CancelRegistry:
    return (RedisCancelRegistry(bus) if bus.label == "redis"
            else InProcessCancelRegistry())

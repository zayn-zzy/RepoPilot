"""Worker bus (§13) — Redis Streams for jobs and per-run events, with a
consumer group, ACK, stale-entry reclaim (worker crash recovery) and a
dead-letter stream. ``InProcessBus`` implements the same contract in
memory for dev/test environments without Redis — the label always says
which transport is running (never pretend)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

JOBS_STREAM = "repopilot:jobs"
DEAD_STREAM = "repopilot:dead"
GROUP = "repopilot-workers"
EVENT_STREAM = "repopilot:events:{run_id}"
MAX_EVENT_LEN = 10_000


@dataclass
class Job:
    job_id: str
    payload: dict[str, Any]


class BusError(RuntimeError):
    """The bus is unavailable — the message says exactly why."""


class EventBus:
    """Transport contract: jobs in, jobs out (ack/reclaim), events out."""

    label = "bus"

    async def push_job(self, payload: dict) -> None:  # pragma: no cover
        raise NotImplementedError

    async def read_job(self, block_ms: int | None = None) -> Job | None:  # pragma: no cover
        raise NotImplementedError

    async def ack_job(self, job_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def reclaim_stale(self, min_idle_ms: int = 30_000) -> list[Job]:  # pragma: no cover
        raise NotImplementedError

    async def dead_letter(self, job_id: str, payload: dict, error: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def publish_event(self, run_id: str, event: dict) -> None:  # pragma: no cover
        raise NotImplementedError

    async def tail_events(self, run_id: str, after_seq: int,
                          block_ms: int) -> list[dict]:  # pragma: no cover
        """Events with seq > after_seq — blocking (SSE tail). History
        (reconnect catch-up) always comes from the SQLite store; the
        seq is the one id space across both."""


class RedisBus(EventBus):
    """The real transport: Redis Streams + a consumer group. Workers
    crash safely — pending entries are reclaimed by the next worker."""

    label = "redis"

    def __init__(self, url: str = "redis://127.0.0.1:6379/0",
                 worker_id: str | None = None):
        self.url = url
        self.worker_id = worker_id or uuid.uuid4().hex[:8]
        self._client = None
        self._cursors: dict[str, str] = {}   # run_id -> last stream entry id

    async def _redis(self):
        if self._client is None:
            import redis.asyncio as aioredis
            # Blocking XREAD blocks the socket for up to 15s — the
            # client socket_timeout must be None (or > block), else
            # redis-py raises TimeoutError and kills the SSE stream
            # (real find: the first live acceptance died after ~5s).
            self._client = aioredis.from_url(
                self.url, decode_responses=True,
                socket_timeout=None, socket_connect_timeout=10)
        return self._client

    async def _ensure_group(self, r) -> None:
        try:
            await r.xgroup_create(JOBS_STREAM, GROUP, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def push_job(self, payload: dict) -> None:
        r = await self._redis()
        await r.xadd(JOBS_STREAM, {"payload": _dump(payload)})

    async def read_job(self, block_ms: int | None = None) -> Job | None:
        r = await self._redis()
        await self._ensure_group(r)
        kwargs = {"count": 1}
        if block_ms is not None:
            kwargs["block"] = block_ms
        resp = await r.xreadgroup(GROUP, self.worker_id,
                                  {JOBS_STREAM: ">"}, **kwargs)
        for _stream, entries in resp:
            for job_id, fields in entries:
                return Job(job_id=job_id, payload=_load(fields["payload"]))
        return None

    async def ack_job(self, job_id: str) -> None:
        r = await self._redis()
        await r.xack(JOBS_STREAM, GROUP, job_id)
        await r.xdel(JOBS_STREAM, job_id)

    async def reclaim_stale(self, min_idle_ms: int = 30_000) -> list[Job]:
        """Reclaim entries whose worker died without ACK (crash
        recovery — a pending job is never lost, §33)."""
        r = await self._redis()
        await self._ensure_group(r)
        jobs: list[Job] = []
        # redis-py 8.x: [next_cursor, [claims], [deleted_ids]] where
        # each claim is an (entry_id, fields) pair (verified against a
        # live redis-server: pending entry reclaimed correctly).
        _cursor, claims, _deleted = await r.xautoclaim(
            JOBS_STREAM, GROUP, self.worker_id,
            min_idle_time=min_idle_ms, count=10)
        for job_id, fields in claims:
            jobs.append(Job(job_id=job_id, payload=_load(fields["payload"])))
        return jobs

    async def dead_letter(self, job_id: str, payload: dict,
                          error: str) -> None:
        r = await self._redis()
        await r.xadd(DEAD_STREAM, {"payload": _dump(payload), "error": error})
        if job_id:
            await r.xack(JOBS_STREAM, GROUP, job_id)

    async def publish_event(self, run_id: str, event: dict) -> None:
        r = await self._redis()
        await r.xadd(EVENT_STREAM.format(run_id=run_id),
                     {"event": _dump(event)},
                     maxlen=MAX_EVENT_LEN, approximate=True)

    async def tail_events(self, run_id: str, after_seq: int,
                          block_ms: int) -> list[dict]:
        r = await self._redis()
        stream = EVENT_STREAM.format(run_id=run_id)
        # Start at 0-0, NOT "$": events published between the SQLite
        # replay and the first tail call must not be skipped — the
        # per-seq filter discards everything already delivered.
        cursor = self._cursors.get(run_id, "0-0")
        resp = await r.xread({stream: cursor}, block=block_ms, count=100)
        out: list[dict] = []
        for _stream, entries in resp:
            for entry_id, fields in entries:
                self._cursors[run_id] = entry_id
                event = _load(fields["event"])
                if int(event.get("seq", 0)) > after_seq:
                    out.append(event)
        return out

    async def heartbeat(self, ttl_s: int = 30) -> None:
        r = await self._redis()
        await r.set(f"repopilot:heartbeat:{self.worker_id}", "alive",
                    ex=ttl_s)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class InProcessBus(EventBus):
    """Same contract, in-memory — dev/tests without a Redis server.
    Crash recovery is not simulated (there is no crash between two
    threads sharing memory); the label says exactly what this is."""

    label = "in-process"

    def __init__(self):
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._events: dict[str, dict[str, dict]] = {}  # run_id -> entry_id -> event
        self._tail_waiters: dict[str, list[asyncio.Future]] = {}
        self._dead: list[dict] = []

    async def push_job(self, payload: dict) -> None:
        await self._jobs.put(("job", uuid.uuid4().hex[:12], payload))

    async def read_job(self, block_ms: int | None = None) -> Job | None:
        try:
            _kind, job_id, payload = await asyncio.wait_for(
                self._jobs.get(), timeout=None if block_ms is None
                else block_ms / 1000)
        except asyncio.TimeoutError:
            return None
        return Job(job_id=job_id, payload=payload)

    async def ack_job(self, job_id: str) -> None:
        pass  # entries leave the queue when read

    async def reclaim_stale(self, min_idle_ms: int = 30_000) -> list[Job]:
        return []  # in-process: nothing survives a crash to reclaim

    async def dead_letter(self, job_id: str, payload: dict,
                          error: str) -> None:
        self._dead.append({"job_id": job_id, "payload": payload,
                           "error": error})

    async def publish_event(self, run_id: str, event: dict) -> None:
        entry_id = str(len(self._events.setdefault(run_id, {})) + 1)
        self._events[run_id][entry_id] = event
        for fut in self._tail_waiters.pop(run_id, []):
            if not fut.done():
                fut.set_result([event])

    async def tail_events(self, run_id: str, after_seq: int,
                          block_ms: int) -> list[dict]:
        events = self._events.get(run_id, {})
        later = [e for e in sorted(events.values(), key=lambda e: e["seq"])
                 if int(e.get("seq", 0)) > after_seq]
        if later:
            return later
        fut = asyncio.get_running_loop().create_future()
        self._tail_waiters.setdefault(run_id, []).append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=block_ms / 1000)
        except asyncio.TimeoutError:
            return []

    async def heartbeat(self, ttl_s: int = 30) -> None:
        pass

    async def close(self) -> None:
        pass


def _dump(obj: dict) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _load(text: str) -> dict:
    import json
    return json.loads(text)

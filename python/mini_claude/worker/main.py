"""The worker — consumes jobs from the bus (Redis Streams consumer
group, or the in-process queue in dev), executes runs, ACKs, dead-
letters failures, reclaims stale entries (crash recovery), heartbeats.

    python -m mini_claude.worker.main
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

log = logging.getLogger("repopilot.worker")

STALE_CLAIM_MS = 30_000     # a job un-ACKed this long = its worker died
BLOCK_MS = 5_000


async def consume_forever(bus, executor, cancel_registry) -> None:
    """The consume loop: reclaim crash-orphaned jobs, then read new
    ones; ACK on success, dead-letter on failure (the error and the
    payload stay inspectable), heartbeat while idle."""
    while True:
        try:
            for job in await bus.reclaim_stale(min_idle_ms=STALE_CLAIM_MS):
                log.warning("reclaimed stale job %s (worker crash recovery)",
                            job.job_id)
                await _run_job(bus, executor, cancel_registry, job)
            job = await bus.read_job(block_ms=BLOCK_MS)
            if job is None:
                await bus.heartbeat()
                continue
            await _run_job(bus, executor, cancel_registry, job)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("consumer loop error (continuing): %s", e)
            await asyncio.sleep(1)


async def _run_job(bus, executor, cancel_registry, job) -> None:
    run_id = job.payload.get("run_id", "")
    await cancel_registry.sync(run_id)   # cancellations requested elsewhere
    try:
        await executor.execute(run_id)
    except Exception as e:
        await bus.dead_letter(job.job_id, job.payload, str(e))
        log.error("job %s dead-lettered: %s", job.job_id, e)
        return
    await bus.ack_job(job.job_id)


async def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from ..api.config import Settings
    from ..events.publisher import EventPublisher
    from ..events.store import EventStore
    from ..persistence.database import session_factory
    from .bus import RedisBus
    from .cancel import build_cancel_registry
    from .executor import RunExecutor

    settings = Settings.from_env()
    redis_url = os.environ.get("REPOPILOT_REDIS_URL")
    if not redis_url:
        print("error: the standalone worker needs REPOPILOT_REDIS_URL "
              "(the in-process bus only exists inside the API process "
              "in dev mode)", file=sys.stderr)
        return 1
    bus = RedisBus(url=redis_url)
    cancel_registry = build_cancel_registry(bus)
    publisher = EventPublisher(EventStore(session_factory(settings.db_url)),
                               bus)
    await publisher.start()
    executor = RunExecutor(settings, session_factory(settings.db_url),
                           publisher, cancel_registry)
    log.info("worker up: bus=%s worker_id=%s db=%s", bus.label,
             bus.worker_id, settings.db_url)
    try:
        await consume_forever(bus, executor, cancel_registry)
    finally:
        await publisher.stop()
        await bus.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

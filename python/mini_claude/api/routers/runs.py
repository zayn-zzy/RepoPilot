"""Run API (Web Phase 5) — the §12 async contract:

    POST /runs → 202 (row + job pushed, never blocks on the agent)
    worker consumes → events stream → SSE to the browser

Cancel (§45) and retry (§33) are REST; the event stream replays from
SQLite (Last-Event-ID) and tails the bus — one seq id space."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from ...application import ApplicationError, PlanningService
from ...events.schema import make_event
from ...events.store import EventStore
from ...persistence.database import session_factory
from ...persistence.models import Run
from ..dependencies import get_db
from ..errors import envelope, request_id
from ..schemas import RunCreateRequest, RunOut
from .repositories import get_registry

log = logging.getLogger("repopilot.api")
router = APIRouter(prefix="/api/v1", tags=["runs"])

_TERMINAL = ("completed", "failed", "cancelled")


def _bus(request: Request):
    return request.app.state.bus


def _publisher(request: Request):
    return request.app.state.publisher


def _cancels(request: Request):
    return request.app.state.cancel_registry


def _store(request: Request):
    return EventStore(session_factory(request.app.state.settings.db_url))


@router.post("/repositories/{repo_id}/runs", status_code=202)
async def create_run(repo_id: str, body: RunCreateRequest,
                     registry=Depends(get_registry),
                     session: Session = Depends(get_db),
                     request: Request = None) -> JSONResponse:
    # the repo must exist (its path is what the worker will run in)
    registry.path_of(repo_id, session=session)
    req = PlanningService().parse(body.requirement)
    run = Run(
        repository_id=repo_id,
        requirement_title=req.title or body.requirement,
        requirement_description=req.description,
        requirement_kind=req.kind.value,
        model=body.model, jobs=body.jobs, sandbox=body.sandbox,
        commit_enabled=body.commit,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    publisher = _publisher(request)
    await publisher.publish(make_event(run.id, "run.created",
                                       message=req.title))
    await publisher.publish(make_event(run.id, "run.queued",
                                       payload={"repository_id": repo_id}))
    await _bus(request).push_job({"run_id": run.id,
                                  "repository_id": repo_id})
    return JSONResponse(status_code=202, content=envelope(
        {"run_id": run.id, "status": run.status,
         "bus": _bus(request).label},
        request_id=request_id(request)))


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_db),
            request: Request = None) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise ApplicationError("RUN_NOT_FOUND", f"run {run_id!r} not found")
    from ...persistence.models import RunEvent
    count = session.query(RunEvent).filter(
        RunEvent.run_id == run_id).count()
    return envelope(RunOut(**run.to_dict()).model_dump(),
                    meta={"events": count},
                    request_id=request_id(request))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, session: Session = Depends(get_db),
                     request: Request = None) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise ApplicationError("RUN_NOT_FOUND", f"run {run_id!r} not found")
    if run.status in _TERMINAL:
        raise ApplicationError(
            "RUN_NOT_CANCELLABLE",
            f"run {run_id!r} is already {run.status}")
    run.cancel_requested = True
    if run.status != "queued":
        run.status = "cancelling"
    else:
        run.status = "cancelled"   # never started — cancel is final
    session.commit()
    cancels = _cancels(request)
    await cancels.request_cancel(run_id)
    if run.status == "cancelled":
        await _publisher(request).publish(make_event(run_id, "run.cancelled"))
    return envelope({"run_id": run_id, "status": run.status},
                    request_id=request_id(request))


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry_run(run_id: str, session: Session = Depends(get_db),
                    request: Request = None) -> JSONResponse:
    original = session.get(Run, run_id)
    if original is None:
        raise ApplicationError("RUN_NOT_FOUND", f"run {run_id!r} not found")
    run = Run(
        repository_id=original.repository_id,
        original_run_id=run_id,
        requirement_title=original.requirement_title,
        requirement_description=original.requirement_description,
        requirement_kind=original.requirement_kind,
        model=original.model, jobs=original.jobs,
        sandbox=original.sandbox,
        commit_enabled=original.commit_enabled,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    publisher = _publisher(request)
    await publisher.publish(make_event(run.id, "run.created"))
    await publisher.publish(make_event(run.id, "run.queued",
                                       payload={"repository_id":
                                                original.repository_id}))
    await _bus(request).push_job({"run_id": run.id,
                                  "repository_id":
                                  original.repository_id})
    return JSONResponse(status_code=202, content=envelope(
        {"run_id": run.id, "status": run.status,
         "original_run_id": run_id},
        request_id=request_id(request)))


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str,
                     session: Session = Depends(get_db),
                     request: Request = None) -> StreamingResponse:
    """§15: text/event-stream with Last-Event-ID reconnect. History is
    replayed from the durable store; the live tail comes from the bus
    (push in-process / stream in Redis); the stream ends when the run
    is terminal and every event has been delivered."""
    run = session.get(Run, run_id)
    if run is None:
        raise ApplicationError("RUN_NOT_FOUND", f"run {run_id!r} not found")
    store = _store(request)
    bus = _bus(request)
    last = request.headers.get("Last-Event-ID") if request else None
    after_seq = int(last) if last and str(last).isdigit() else 0
    delivered = after_seq

    async def gen():
        nonlocal delivered
        for event in store.replay(run_id, after_seq=after_seq):
            delivered = max(delivered, event["seq"])
            yield _sse(event)
        while True:
            # The worker commits in its own session — expire this
            # session's identity map or get() returns the stale object
            # and the stream never sees the terminal status.
            session.expire_all()
            row = session.get(Run, run_id)
            terminal = row is not None and row.status in _TERMINAL
            fresh = store.last_seq(run_id)
            if fresh <= delivered:
                if terminal:
                    return
            new = await bus.tail_events(run_id, delivered, block_ms=15_000)
            if not new:
                yield ": keepalive\n\n"
                continue
            for event in new:
                if event["seq"] > delivered:
                    delivered = event["seq"]
                    yield _sse(event)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "X-Accel-Buffering": "no",     # nginx: never buffer SSE
                 "Connection": "keep-alive"})


def _sse(event: dict) -> str:
    data = json.dumps(event, ensure_ascii=False)
    return (f"event: {event['event_type']}\n"
            f"id: {event['seq']}\n"
            f"data: {data}\n\n")

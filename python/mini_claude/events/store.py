"""EventStore — the durable record of every RunEvent (SQLite is the
Source of Truth; Redis is transport, §13). Replay support powers the
SSE Last-Event-ID reconnect contract (§15)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..persistence.models import RunEvent as RunEventRow
from .schema import RunEvent


class EventStore:
    def __init__(self, session_factory):
        self._factory = session_factory

    def append(self, event: RunEvent, *, session: Session | None = None) -> dict:
        """Persist one event; returns the stored row as a dict (with
        its per-run ``seq`` — the SSE event id)."""
        own = session is None
        session = session or self._factory()
        try:
            current = session.scalars(
                select(func.max(RunEventRow.seq)).where(
                    RunEventRow.run_id == event.run_id)).one()
            seq = (current or 0) + 1
            row = RunEventRow(
                run_id=event.run_id, seq=seq, event_id=event.event_id,
                event_type=event.event_type, task_id=event.task_id,
                agent_id=event.agent_id, status=event.status,
                message=event.message, payload_json=_dump(event.payload),
            )
            session.add(row)
            session.commit()
            return self._as_dict(row)
        finally:
            if own:
                session.close()

    def replay(self, run_id: str, *, after_seq: int | None = None,
               session: Session | None = None) -> list[dict]:
        """Events after ``after_seq`` (SSE Last-Event-ID reconnect)."""
        own = session is None
        session = session or self._factory()
        try:
            stmt = select(RunEventRow).where(
                RunEventRow.run_id == run_id).order_by(RunEventRow.seq)
            if after_seq is not None:
                stmt = stmt.where(RunEventRow.seq > after_seq)
            return [self._as_dict(r) for r in session.scalars(stmt)]
        finally:
            if own:
                session.close()

    def last_seq(self, run_id: str, *, session: Session | None = None) -> int:
        own = session is None
        session = session or self._factory()
        try:
            return session.scalars(
                select(func.max(RunEventRow.seq)).where(
                    RunEventRow.run_id == run_id)).one() or 0
        finally:
            if own:
                session.close()

    @staticmethod
    def _as_dict(row: RunEventRow) -> dict:
        return {
            "seq": row.seq, "event_id": row.event_id,
            "run_id": row.run_id, "event_type": row.event_type,
            "task_id": row.task_id, "agent_id": row.agent_id,
            "status": row.status, "message": row.message,
            "payload": _load(row.payload_json),
            "timestamp": row.created_at.isoformat()
            if row.created_at else None,
        }


def _dump(payload: dict) -> str:
    import json
    return json.dumps(payload or {}, ensure_ascii=False)


def _load(text: str | None) -> dict:
    import json
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}

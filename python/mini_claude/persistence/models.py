"""Web persistence models (Web Phase 3+) — the application database
tables (§14). ``owner_id`` exists from the start (§20: the data model
reserves ownership even while only the owner role exists)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uuid)
    repository_id: Mapped[str | None] = mapped_column(String(32),
                                                      nullable=True)
    requirement_title: Mapped[str] = mapped_column(String(500),
                                                   default="")
    requirement_kind: Mapped[str] = mapped_column(String(64), default="")
    requirement_description: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    planner: Mapped[str] = mapped_column(String(32), default="deterministic")
    nodes_json: Mapped[str] = mapped_column(Text, default="[]")
    edges_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False,
                                      unique=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True,
                                                 default=None)  # §20
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now,
                                                 onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                        nullable=True,
                                                        default=None)
    initialized_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                            nullable=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                             nullable=True)
    last_index_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    index_files: Mapped[int] = mapped_column(Integer, default=0)
    index_symbols: Mapped[int] = mapped_column(Integer, default=0)
    index_errors: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "path": self.path,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "initialized_at": (self.initialized_at.isoformat()
                               if self.initialized_at else None),
            "last_indexed_at": (self.last_indexed_at.isoformat()
                                if self.last_indexed_at else None),
            "last_index_note": self.last_index_note,
            "index_files": self.index_files,
            "index_symbols": self.index_symbols,
            "index_errors": self.index_errors,
        }


RUN_STATUSES = ("queued", "planning", "running", "verifying", "repairing",
                "reviewing", "completed", "failed", "cancelled",
                "cancelling")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    default=_uuid)
    repository_id: Mapped[str | None] = mapped_column(String(32),
                                                      nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    original_run_id: Mapped[str | None] = mapped_column(String(32),
                                                        nullable=True)  # retry link
    requirement_title: Mapped[str] = mapped_column(String(500), default="")
    requirement_description: Mapped[str] = mapped_column(Text, default="")
    requirement_kind: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    jobs: Mapped[int] = mapped_column(Integer, default=1)
    sandbox: Mapped[str] = mapped_column(String(16), default="auto")
    commit_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worktree_task_id: Mapped[str | None] = mapped_column(String(64),
                                                         nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id, "repository_id": self.repository_id,
            "plan_id": self.plan_id, "original_run_id": self.original_run_id,
            "requirement_title": self.requirement_title,
            "requirement_description": self.requirement_description,
            "requirement_kind": self.requirement_kind,
            "model": self.model, "jobs": self.jobs, "sandbox": self.sandbox,
            "commit_enabled": self.commit_enabled,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
            "worktree_task_id": self.worktree_task_id,
            "error": self.error,
            "verification": (json.loads(self.verification_json)
                             if self.verification_json else None),
            "repair": (json.loads(self.repair_json)
                       if self.repair_json else None),
            "diff": json.loads(self.diff_json) if self.diff_json else None,
            "pr_title": self.pr_title,
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)   # per-run ordering (SSE ids)
    event_id: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

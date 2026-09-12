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

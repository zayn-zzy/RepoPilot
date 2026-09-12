"""Persistence foundation (Web Phase 2): the SQLAlchemy engine/session
for the application database (`.repopilot/app.db` — separate from the
repository index db). WP3 adds the models; here the engine exists so
/ready can prove a real connection."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    """All Web models derive from this (tables appear in WP3+)."""


_engines: dict[str, object] = {}
_factories: dict[str, sessionmaker] = {}


def engine_for(db_url: str):
    """One engine per db URL (tests use tmp dbs). The db file's parent
    directory is created here so a fresh workspace can connect."""
    if db_url not in _engines:
        from pathlib import Path
        Path(db_url).parent.mkdir(parents=True, exist_ok=True)
        _engines[db_url] = create_engine(
            f"sqlite:///{db_url}", connect_args={"check_same_thread": False})
    return _engines[db_url]


def session_factory(db_url: str) -> sessionmaker:
    if db_url not in _factories:
        _factories[db_url] = sessionmaker(bind=engine_for(db_url))
    return _factories[db_url]


def ping(db_url: str) -> bool:
    """A real connection check for /ready."""
    try:
        with engine_for(db_url).connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def init_schema(db_url: str) -> None:
    """Create all Web tables (idempotent). Imports the models at call
    time so create_all sees every table (avoids a module cycle)."""
    from . import models  # noqa: F401
    Base.metadata.create_all(engine_for(db_url))

"""API dependencies (Web Phase 2): settings and the database session."""

from __future__ import annotations

from fastapi import Request

from ..persistence.database import session_factory
from .config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request):
    """One SQLAlchemy session per request (closed by the dependency
    teardown)."""
    settings: Settings = request.app.state.settings
    factory = session_factory(settings.db_url)
    session = factory()
    try:
        yield session
    finally:
        session.close()

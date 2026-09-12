"""The RepoPilot Web API (Web Phase 2+): FastAPI on top of the
Application Services — the API never touches Core directly."""

from .main import app, create_app

__all__ = ["app", "create_app"]

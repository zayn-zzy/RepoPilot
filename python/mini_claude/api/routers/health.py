"""Health router (Web Phase 2): /health (liveness) and /ready
(readiness — a real db connection + an existing workspace root)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...persistence import database
from ..config import VERSION
from ..errors import envelope, request_id

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    return envelope({"status": "ok"},
                    meta={"version": VERSION},
                    request_id=request_id(request))


@router.get("/ready")
def ready(request: Request) -> dict:
    from fastapi.responses import JSONResponse
    settings = request.app.state.settings
    problems: list[str] = []
    if not settings.workspace_root.is_dir():
        problems.append(f"workspace root does not exist: {settings.workspace_root}")
    if not database.ping(settings.db_url):
        problems.append("database is unreachable")
    if problems:
        return JSONResponse(
            status_code=503,
            content=envelope(
                {"status": "not_ready", "reasons": problems},
                request_id=request_id(request)))
    return envelope({"status": "ready"},
                    request_id=request_id(request))

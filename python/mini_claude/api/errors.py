"""API error handling and the §11 response envelope.

Success: {"data": ..., "meta": ..., "request_id": "..."}
Error:   {"error": {"code": "...", "message": "...", "details": {}}, "request_id": "..."}

No Python traceback ever reaches the browser — the real exception is
logged server-side only (§11/§48)."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..application import ApplicationError

log = logging.getLogger("repopilot.api")

# ApplicationError.code → HTTP status (everything unmapped is a 500).
_STATUS_BY_CODE = {
    "REPOSITORY_NOT_FOUND": 404,
    "MODULE_NOT_FOUND": 404,
    "RUN_NOT_FOUND": 404,
    "PLAN_NOT_FOUND": 404,
    "API_KEY_REQUIRED": 400,
    "VALIDATION_ERROR": 400,
    "NOT_A_GIT_REPOSITORY": 400,
    "PATH_OUTSIDE_WORKSPACE": 400,
    "ALREADY_EXISTS": 409,
    "RUN_NOT_CANCELLABLE": 409,
}


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid or uuid.uuid4().hex[:12]


def envelope(data=None, *, meta: dict | None = None, request_id: str = "") -> dict:
    return {"data": data, "meta": meta or {},
            "request_id": request_id}


def error_envelope(code: str, message: str, *,
                   details: dict | None = None,
                   request_id: str = "") -> dict:
    return {"error": {"code": code, "message": message,
                      "details": details or {}},
            "request_id": request_id}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def _application_error(request: Request, exc: ApplicationError):
        status = _STATUS_BY_CODE.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content=error_envelope(exc.code, exc.message,
                                   details=exc.details,
                                   request_id=request_id(request)))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled API error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_envelope("INTERNAL_ERROR",
                                   "internal server error — see the server log",
                                   request_id=request_id(request)))

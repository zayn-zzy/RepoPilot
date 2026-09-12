"""The FastAPI app (Web Phase 2 foundation).

    uvicorn repopilot.api.main:app --reload

create_app(settings) is the test seam; the module-level ``app`` reads
its settings from the environment."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings, VERSION
from .errors import register_error_handlers
from .middleware import RequestIdMiddleware, install_cors
from .routers import health, repositories


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import logging
        from ..persistence import database
        # The workspace root is the deployment's mounted volume — it is
        # NEVER created here. Without it the app starts degraded and
        # /ready reports the problem (instead of crashing at boot).
        if settings.workspace_root.is_dir():
            database.init_schema(settings.db_url)
        else:
            logging.getLogger("repopilot.api").warning(
                "workspace root %s does not exist — starting degraded "
                "(/ready will report it)", settings.workspace_root)
        yield

    app = FastAPI(
        title="RepoPilot API",
        description="RepoPilot — autonomous software engineering for "
                    "large code repositories (Web API).",
        version=VERSION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    install_cors(app, settings.cors_origins)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(repositories.router)
    return app


app = create_app()

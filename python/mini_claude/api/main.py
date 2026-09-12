"""The FastAPI app (Web Phase 2 foundation).

    uvicorn repopilot.api.main:app --reload

create_app(settings) is the test seam; the module-level ``app`` reads
its settings from the environment."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

import asyncio

from .config import Settings, VERSION
from .errors import register_error_handlers
from .middleware import RequestIdMiddleware, install_cors
from .routers import ask, health, plans, repositories, runs


def create_app(settings: Settings | None = None,
               executor_factory=None) -> FastAPI:
    """``executor_factory(settings, session_factory, publisher,
    cancel_registry) -> RunExecutor`` is the test seam — tests inject a
    scripted executor so the embedded worker runs without an LLM."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import logging
        from ..persistence import database
        from ..worker.bus import InProcessBus, RedisBus
        from ..worker.cancel import build_cancel_registry
        from ..events.publisher import EventPublisher
        from ..events.store import EventStore
        from ..worker.executor import RunExecutor
        from ..worker.main import consume_forever
        log = logging.getLogger("repopilot.api")
        # The workspace root is the deployment's mounted volume — it is
        # NEVER created here. Without it the app starts degraded and
        # /ready reports the problem (instead of crashing at boot).
        if settings.workspace_root.is_dir():
            database.init_schema(settings.db_url)
        else:
            log.warning("workspace root %s does not exist — starting "
                        "degraded (/ready will report it)",
                        settings.workspace_root)
        bus = (RedisBus(url=settings.redis_url) if settings.redis_url
               else InProcessBus())
        if not settings.redis_url:
            log.warning("REPOPILOT_REDIS_URL not set — using the "
                        "in-process bus (single-process dev mode; the "
                        "worker runs inside the API process)")
        app.state.bus = bus
        app.state.publisher = EventPublisher(
            EventStore(database.session_factory(settings.db_url)), bus)
        await app.state.publisher.start()
        app.state.cancel_registry = build_cancel_registry(bus)
        app.state.worker_task = None
        if bus.label == "in-process":
            executor = (executor_factory(settings,
                                         database.session_factory(
                                             settings.db_url),
                                         app.state.publisher,
                                         app.state.cancel_registry)
                        if executor_factory is not None
                        else RunExecutor(
                            settings,
                            database.session_factory(settings.db_url),
                            app.state.publisher, app.state.cancel_registry))
            app.state.worker_task = asyncio.create_task(
                consume_forever(bus, executor, app.state.cancel_registry),
                name="repopilot-worker")
        yield
        if app.state.worker_task is not None:
            app.state.worker_task.cancel()
            try:
                await app.state.worker_task
            except asyncio.CancelledError:
                pass
        await app.state.publisher.stop()
        await bus.close()

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
    app.include_router(ask.router)
    app.include_router(plans.router)
    app.include_router(runs.router)
    return app


app = create_app()

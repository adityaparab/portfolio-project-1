"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from invoiceops_agent.api import deps
from invoiceops_agent.api.errors import register_error_handlers
from invoiceops_agent.api.health import router as health_router
from invoiceops_agent.api.middleware import IdempotencyMiddleware, InMemoryIdempotencyStore
from invoiceops_agent.api.routes.invoices import router as invoices_router
from invoiceops_agent.api.routes.webhook import router as webhook_router
from invoiceops_agent.api.services.ingest import IngestService
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.obs.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    app.state.engine = deps.build_engine(settings)
    app.state.session_factory = async_sessionmaker(app.state.engine, expire_on_commit=False)
    app.state.object_store = deps.build_object_store(settings)
    app.state.ingest_service = IngestService(
        store=app.state.object_store,
        session_factory=app.state.session_factory,
        settings=settings,
    )
    from invoiceops_agent.api.services.dashboard import DashboardService
    from invoiceops_agent.api.services.decisions import DecisionService
    from invoiceops_agent.api.services.queue import QueueService
    from invoiceops_agent.api.services.trace import TraceService

    app.state.queue_service = QueueService(app.state.session_factory)
    app.state.trace_service = TraceService(app.state.session_factory)
    app.state.dashboard_service = DashboardService(app.state.session_factory)
    app.state.graph_runner = None
    app.state.decision_runner_provider = lambda: app.state.graph_runner
    app.state.graph_conn = None
    try:
        # Eager runner: pipeline executes right after ingest. A cold start
        # against an unreachable DB only degrades to no-runner (uploads still
        # queue runs; a later boot picks them up) rather than failing boot.
        runner, conn = await deps.build_graph_runner(
            settings, app.state.session_factory, app.state.object_store
        )
        app.state.graph_runner = runner
        app.state.graph_conn = conn
        app.state.decision_service = DecisionService(
            app.state.session_factory, runner_provider=app.state.decision_runner_provider
        )
    except Exception:
        logger.exception("graph runner unavailable at startup — uploads queue without processing")
    try:
        yield
    finally:
        if app.state.graph_conn is not None:
            await app.state.graph_conn.close()
        await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="InvoiceOps Agent API",
        version="0.1.0",
        description="Agentic, human-in-the-loop invoice processing for Source-to-Pay.",
        lifespan=lifespan,
    )
    app.state.settings = settings or Settings()
    app.state.idempotency_store = InMemoryIdempotencyStore()

    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app.state.settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Idempotency-Key"],
    )

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(invoices_router)
    app.include_router(webhook_router)
    from invoiceops_agent.api.routes.evals import router as evals_router
    from invoiceops_agent.api.routes.exceptions import router as exceptions_router
    from invoiceops_agent.api.routes.metrics import router as metrics_router
    from invoiceops_agent.api.routes.runs import router as runs_router

    app.include_router(exceptions_router)
    app.include_router(runs_router)
    app.include_router(metrics_router)
    app.include_router(evals_router)

    _mount_spa(app, Path(app.state.settings.ui_dist) if app.state.settings.ui_dist else None)
    return app


def _mount_spa(app: FastAPI, dist: Path | None) -> None:
    """Serve the built SPA at / with client-route fallback (single-port deploy).

    Registered last: every API route (including /docs) keeps precedence.
    Unknown non-API paths return index.html so React Router owns deep links;
    hashed /assets get immutable caching, index.html stays no-cache so
    deploys propagate. No directory when the UI isn't built (dev profile).
    """
    if dist is None or not (dist / "index.html").is_file():
        return
    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            # The SPA calls same-origin /v1/* in this mode; /api/* is the dev
            # proxy prefix — surface a real 404 instead of HTML.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not an API path")
        candidate = (root / full_path).resolve() if full_path else root / "index.html"
        try:
            candidate.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
        if full_path and candidate.is_file():
            immutable = full_path.startswith("assets/")
            return FileResponse(
                candidate,
                headers={
                    "Cache-Control": (
                        "public, max-age=31536000, immutable" if immutable else "no-cache"
                    )
                },
            )
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


app = create_app()

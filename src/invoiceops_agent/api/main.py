"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
    app.state.graph_runner = None
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
    return app


app = create_app()

"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from invoiceops_agent.api.errors import register_error_handlers
from invoiceops_agent.api.health import router as health_router
from invoiceops_agent.api.middleware import IdempotencyMiddleware, InMemoryIdempotencyStore
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.obs.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    yield


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
    return app


app = create_app()

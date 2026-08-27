"""FastAPI dependencies: object store, ingest service, engine builders."""

import json
from collections.abc import Mapping

import psycopg
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from invoiceops_agent.api.services.ingest import IngestService
from invoiceops_agent.api.services.queue import QueueService
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.graph import runtime
from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.retries import RetryPolicy
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.storage.minio import MinioObjectStore, ObjectStore


def get_object_store(request: Request) -> ObjectStore:
    store: ObjectStore | None = getattr(request.app.state, "object_store", None)
    if store is None:
        raise RuntimeError("object_store not initialized on app.state")
    return store


def build_engine(settings: Settings) -> AsyncEngine:
    """Async engine bound to the app-role DSN."""
    return create_async_engine(
        settings.database_dsn.replace("postgresql://", "postgresql+asyncpg://", 1),
        pool_pre_ping=True,
    )


def build_object_store(settings: Settings) -> ObjectStore:
    return MinioObjectStore.from_settings(settings, bucket=settings.minio_bucket)


async def build_graph_runner(
    settings: Settings,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    object_store: ObjectStore,
) -> tuple[GraphRunner, psycopg.AsyncConnection[Mapping[str, object]]]:
    """Runner + its long-lived checkpointer connection (caller closes it)."""
    gateway = runtime.build_gateway(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        token_budgets=settings.gateway_token_budgets,
        timeout_seconds=settings.gateway_timeout_seconds,
        infra_retries=settings.gateway_infra_retries,
        alias_model_map=json.loads(settings.gateway_model_map_json or "{}"),
    )
    context = runtime.build_context(sessions=session_factory, store=object_store, gateway=gateway)
    saver, conn = await open_saver(settings.alembic_dsn or settings.database_dsn)
    policy = RetryPolicy(
        attempts=settings.graph_retry_attempts,
        base_delay=settings.graph_retry_base_delay_seconds,
        max_delay=settings.graph_retry_max_delay_seconds,
        jitter=settings.graph_retry_jitter_seconds,
    )
    return GraphRunner(context, saver, retry_policy=policy), conn


def get_ingest_service(request: Request) -> IngestService:
    service: IngestService | None = getattr(request.app.state, "ingest_service", None)
    if service is None:
        raise RuntimeError("ingest_service not initialized on app.state")
    return service


def get_queue_service(request: Request) -> QueueService:
    service: QueueService | None = getattr(request.app.state, "queue_service", None)
    if service is None:
        raise RuntimeError("queue_service not initialized on app.state")
    return service


def get_graph_runner(request: Request) -> GraphRunner | None:
    """The pipeline runner, or None on a cold start without a reachable DB —
    detail aggregates then degrade to ``state_available: false``."""
    return getattr(request.app.state, "graph_runner", None)

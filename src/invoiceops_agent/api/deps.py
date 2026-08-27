"""FastAPI dependencies: object store, ingest service, engine builders."""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from invoiceops_agent.api.services.ingest import IngestService
from invoiceops_agent.api.settings import Settings
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


def get_ingest_service(request: Request) -> IngestService:
    service: IngestService | None = getattr(request.app.state, "ingest_service", None)
    if service is None:
        raise RuntimeError("ingest_service not initialized on app.state")
    return service

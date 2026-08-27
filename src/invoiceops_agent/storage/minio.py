"""Object storage (MinIO/S3) access — raw invoice documents.

Content-addressed keys: ``raw/{sha256}``. The original bytes are stored once;
``invoices.doc_ref`` references the key. Blocking ``minio`` SDK calls are
dispatched to a thread (AGENTS.md async discipline).
"""

from typing import Protocol

import anyio
from minio import Minio

from invoiceops_agent.api.settings import Settings


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str: ...


class MinioObjectStore:
    """Thin async wrapper over the (sync) MinIO SDK."""

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings, bucket: str = "raw-invoices") -> "MinioObjectStore":
        # base URL like http://localhost:9000 → (host, port, secure)
        url = settings.minio_base_url.removeprefix("http://").removeprefix("https://")
        secure = settings.minio_base_url.startswith("https://")
        host, _, port = url.partition(":")
        client = Minio(
            f"{host}:{port or '9000'}",
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
        )
        return cls(client, bucket)

    async def _ensure_bucket(self) -> None:
        found = await anyio.to_thread.run_sync(self._client.bucket_exists, self._bucket)
        if not found:
            await anyio.to_thread.run_sync(self._client.make_bucket, self._bucket)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        import io

        await self._ensure_bucket()
        await anyio.to_thread.run_sync(
            lambda: self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        )

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        from datetime import timedelta

        return await anyio.to_thread.run_sync(
            lambda: self._client.presigned_get_object(
                self._bucket, key, expires=timedelta(seconds=expires_seconds)
            )
        )

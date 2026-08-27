"""Ingest service: upload → content hash → MinIO → invoice/run rows → ledger."""

import hashlib
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.api.schemas.invoices import InvoiceAccepted
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.db.models import Invoice, LedgerEntry, Run
from invoiceops_agent.storage.minio import ObjectStore

# Versioned config placeholder until graph/prompt versioning lands (#14/#26).
GRAPH_VERSION = "0.1.0"


class DuplicateInvoiceError(HTTPException):
    """Exact content hash already ingested (routing refined in issue #13)."""

    def __init__(self, existing_invoice_id: int, content_hash: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Duplicate invoice content.",
                "existing_invoice_id": existing_invoice_id,
                "content_hash": content_hash,
            },
        )


@dataclass(frozen=True)
class IngestResult:
    accepted: InvoiceAccepted
    object_key: str


class IngestService:
    def __init__(
        self,
        store: ObjectStore,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._store = store
        self._sessions = session_factory
        self._settings = settings

    async def ingest(self, upload: UploadFile) -> IngestResult:
        data = await self._read_and_validate(upload)
        content_hash = hashlib.sha256(data).hexdigest()
        key = f"raw/{content_hash}"

        async with self._sessions() as session:
            try:
                invoice, run = await self._create_rows(session, upload, content_hash, key)
                await self._append_ledger(session, invoice, run)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                existing = await self._find_existing(session, content_hash)
                if existing is not None:
                    raise DuplicateInvoiceError(existing, content_hash) from exc
                raise
            await self._store.put(key, data, upload.content_type or "application/octet-stream")

        return IngestResult(
            accepted=InvoiceAccepted(
                invoice_id=invoice.invoice_id,
                run_id=run.run_id,
                content_hash=content_hash,
            ),
            object_key=key,
        )

    async def _read_and_validate(self, upload: UploadFile) -> bytes:
        allowed = set(self._settings.allowed_content_types)
        if upload.content_type not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unsupported content type {upload.content_type!r}; allowed: {sorted(allowed)}"
                ),
            )
        limit = self._settings.max_upload_bytes
        chunks: list[bytes] = []
        size = 0
        while chunk := await upload.read(1 << 20):  # 1 MiB chunks
            size += len(chunk)
            if size > limit:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds {limit} bytes.",
                )
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Empty upload.",
            )
        return data

    async def _create_rows(
        self,
        session: AsyncSession,
        upload: UploadFile,
        content_hash: str,
        key: str,
    ) -> tuple[Invoice, Run]:
        invoice = Invoice(
            content_hash=content_hash,
            doc_ref=key,
            status="RECEIVED",
        )
        session.add(invoice)
        await session.flush()  # allocate invoice_id

        run = Run(
            invoice_id=invoice.invoice_id,
            graph_version=GRAPH_VERSION,
            model_versions={},
            status="QUEUED",
        )
        session.add(run)
        await session.flush()
        return invoice, run

    async def _append_ledger(self, session: AsyncSession, invoice: Invoice, run: Run) -> None:
        entry = LedgerEntry(
            run_id=run.run_id,
            invoice_id=invoice.invoice_id,
            seq=1,
            actor_type="SYSTEM",
            actor_id="ingest",
            event={
                "event": "ingest.accepted",
                "content_hash": invoice.content_hash,
                "doc_ref": invoice.doc_ref,
            },
            policy_version=None,
            prompt_template_version=None,
        )
        session.add(entry)

    async def _find_existing(self, session: AsyncSession, content_hash: str) -> int | None:
        from sqlalchemy import select

        result = await session.execute(
            select(Invoice.invoice_id).where(Invoice.content_hash == content_hash)
        )
        row: Any = result.scalar_one_or_none()
        return int(row) if row is not None else None

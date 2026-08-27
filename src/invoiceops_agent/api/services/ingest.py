"""Ingest service: upload → content hash → MinIO → invoice/run rows → ledger.

Duplicate content (issue #13): the duplicate upload gets its own REJECTED run
on the ORIGINAL invoice plus a SYSTEM ledger entry referencing the original —
the same outcome the graph's Reject terminal node produces (wired in #25).
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.api.schemas.invoices import InvoiceAccepted
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.db.models import Invoice, Run
from invoiceops_agent.ledger.api import ActorType, LedgerAppend, writer
from invoiceops_agent.storage.minio import ObjectStore
from invoiceops_agent.versions import CURRENT


@dataclass(frozen=True)
class IngestResult:
    accepted: InvoiceAccepted
    object_key: str
    duplicate: bool = False


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
        data, content_hash = await self._read_and_hash(upload)
        key = f"raw/{content_hash}"

        async with self._sessions() as session:
            try:
                invoice, run = await self._create_rows(session, content_hash, key)
                await self._append_accept_ledger(session, invoice, run)
                await session.commit()
            except IntegrityError:
                # Race-safe: unique content_hash lost the race → duplicate path.
                await session.rollback()
                existing = await self._find_existing(session, content_hash)
                if existing is None:
                    raise
                invoice_id, run_id = await self._record_duplicate(session, existing, content_hash)
                return IngestResult(
                    accepted=InvoiceAccepted(
                        invoice_id=invoice_id,
                        run_id=run_id,
                        content_hash=content_hash,
                        status="REJECTED",
                        duplicate=True,
                    ),
                    object_key=key,
                    duplicate=True,
                )
            await self._store.put(key, data, upload.content_type or "application/octet-stream")

        return IngestResult(
            accepted=InvoiceAccepted(
                invoice_id=invoice.invoice_id,
                run_id=run.run_id,
                content_hash=content_hash,
            ),
            object_key=key,
        )

    async def _read_and_hash(self, upload: UploadFile) -> tuple[bytes, str]:
        allowed = set(self._settings.allowed_content_types)
        if upload.content_type not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unsupported content type {upload.content_type!r}; allowed: {sorted(allowed)}"
                ),
            )
        limit = self._settings.max_upload_bytes
        hasher = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while chunk := await upload.read(1 << 20):  # 1 MiB chunks, hashed streaming
            size += len(chunk)
            if size > limit:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds {limit} bytes.",
                )
            hasher.update(chunk)
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Empty upload.",
            )
        return data, hasher.hexdigest()

    async def _create_rows(
        self, session: AsyncSession, content_hash: str, key: str
    ) -> tuple[Invoice, Run]:
        invoice = Invoice(content_hash=content_hash, doc_ref=key, status="RECEIVED")
        session.add(invoice)
        await session.flush()  # allocate invoice_id

        run = Run(
            invoice_id=invoice.invoice_id,
            graph_version=CURRENT.graph,
            model_versions={},
            status="QUEUED",
        )
        session.add(run)
        await session.flush()
        return invoice, run

    async def _append_accept_ledger(
        self, session: AsyncSession, invoice: Invoice, run: Run
    ) -> None:
        await writer.append(
            session,
            LedgerAppend(
                actor_type=ActorType.SYSTEM,
                actor_id="ingest",
                run_id=run.run_id,
                invoice_id=invoice.invoice_id,
                event={
                    "event": "ingest.accepted",
                    "content_hash": invoice.content_hash,
                    "doc_ref": invoice.doc_ref,
                },
            ),
        )

    async def _record_duplicate(
        self, session: AsyncSession, existing_invoice_id: int, content_hash: str
    ) -> tuple[int, int]:
        """Reject-run + ledger entry on the original invoice; no second copy."""
        run = Run(
            invoice_id=existing_invoice_id,
            graph_version=CURRENT.graph,
            model_versions={},
            route="REJECT",
            status="REJECTED",
        )
        session.add(run)
        await session.flush()
        await writer.append(
            session,
            LedgerAppend(
                actor_type=ActorType.SYSTEM,
                actor_id="ingest",
                run_id=run.run_id,
                invoice_id=existing_invoice_id,
                event={
                    "event": "ingest.duplicate",
                    "content_hash": content_hash,
                    "original_invoice_id": existing_invoice_id,
                    "route": "REJECT",
                },
            ),
        )
        await session.commit()
        return existing_invoice_id, run.run_id

    async def _find_existing(self, session: AsyncSession, content_hash: str) -> int | None:
        result = await session.execute(
            select(Invoice.invoice_id).where(Invoice.content_hash == content_hash)
        )
        row: Any = result.scalar_one_or_none()
        return int(row) if row is not None else None

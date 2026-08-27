"""Extraction agent: raw document → typed InvoiceExtraction (issue #16).

Flow: fetch bytes from the object store (content-addressed ref) → build the
versioned vision prompt → gateway `extract-vision` (schema-validated, guard-
railed, cassette-compatible) → AGENT ledger entry pinning prompt + model.
Malformed output after the gateway's corrective retry escalates as
``GatewayResponseError`` — the agent never guesses and writes no ledger entry
on failure.
"""

import base64
import logging
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.agents.prompts import extract_invoice_v2 as extract_invoice
from invoiceops_agent.gateway_client import GatewayClient
from invoiceops_agent.ledger.api import ActorType, LedgerAppend, writer
from invoiceops_agent.storage.content_type import sniff_content_type
from invoiceops_agent.storage.minio import ObjectStore
from invoiceops_agent.versions import CURRENT, VersionPins

logger = logging.getLogger(__name__)


class ExtractionLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_no: str
    description: str | None = None
    qty: Decimal | None = None
    uom: str | None = None
    unit_price: Decimal | None = None
    tax_code: str | None = None
    line_total: Decimal | None = None


class InvoiceExtraction(BaseModel):
    """Typed extraction contract with per-field confidence (gate input #26)."""

    model_config = ConfigDict(frozen=True)

    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_number: str | None = None
    po_number: str | None = None  # PO reference on the document (match key, #21)
    issue_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    tax_total: Decimal | None = None
    iban: str | None = None
    lines: list[ExtractionLine] = Field(default_factory=list)
    confidences: dict[str, float] = Field(default_factory=dict)

    @field_validator("issue_date", "due_date")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        from datetime import date

        date.fromisoformat(value)  # raises on non-ISO
        return value

    def min_confidence(self) -> float:
        """Minimum across all recorded confidences (gate term w1 — #26)."""
        if not self.confidences:
            return 0.0
        return min(self.confidences.values())


class ExtractionAgent:
    ALIAS = "extract-vision"

    def __init__(
        self,
        store: ObjectStore,
        gateway: GatewayClient,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        # session_factory only needed for the ledger-recording extract(); the
        # document-only path stays DB-free for unit testing.
        self._store = store
        self._gateway = gateway
        self._sessions = session_factory

    async def extract(
        self,
        doc_ref: str,
        content_type: str | None = None,
        *,
        run_id: int | None = None,
        invoice_id: int | None = None,
        scenario: str | None = None,
    ) -> InvoiceExtraction:
        """Extract + record. Raises GatewayResponseError on malformed output."""
        if self._sessions is None:
            raise RuntimeError("ledger-recording extract() requires a session_factory")
        extraction = await self.extract_document(doc_ref, content_type, scenario=scenario)

        async with self._sessions() as session:
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.AGENT,
                    actor_id="extract",
                    run_id=run_id,
                    invoice_id=invoice_id,
                    event={
                        "event": "extract.completed",
                        "doc_ref": doc_ref,
                        "fields_extracted": len(extraction.confidences),
                        "lines": len(extraction.lines),
                        "min_confidence": extraction.min_confidence(),
                    },
                    prompt_version=extract_invoice.PROMPT_VERSION,
                    versions=VersionPins(
                        graph=CURRENT.graph, models={self.ALIAS: "backend-pinned"}
                    ),
                ),
            )
            await session.commit()
        return extraction

    async def extract_document(
        self, doc_ref: str, content_type: str | None = None, *, scenario: str | None = None
    ) -> InvoiceExtraction:
        """Model call only (no ledger) — the unit-testable core."""
        data_b64 = await self._fetch_b64(doc_ref)
        if content_type is None:
            content_type = sniff_content_type(base64.b64decode(data_b64))
        return await self.extract_bytes(data_b64, content_type, doc_ref, scenario=scenario)

    async def extract_bytes(
        self,
        data_b64: str,
        content_type: str,
        doc_ref: str,
        *,
        scenario: str | None = None,
    ) -> InvoiceExtraction:
        """Model call from in-memory base64 — used by eval runners (#19/#45)."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": extract_invoice.SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": extract_invoice.USER_TEMPLATE.format(
                            filename=doc_ref.rsplit("/", 1)[-1]
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{content_type};base64,{data_b64}"},
                    },
                ],
            },
        ]
        result = await self._gateway.complete(
            self.ALIAS,
            messages,
            InvoiceExtraction,
            scenario=scenario,
        )
        if not isinstance(result, InvoiceExtraction):  # pragma: no cover — type-narrow
            raise TypeError("gateway returned unstructured result for extraction")
        return result

    async def _fetch_b64(self, doc_ref: str) -> str:
        """Object store fetch → base64. Presigned GET keeps stores exchangeable."""
        url = await self._store.presigned_get(doc_ref, expires_seconds=30)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode()

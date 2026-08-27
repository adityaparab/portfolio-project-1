"""Integration test: extraction agent with ledger on real Postgres (issue #16)."""

import asyncio
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.agents.extraction import ExtractionAgent, InvoiceExtraction
from invoiceops_agent.db.models import Invoice, LedgerEntry, Run
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient
from invoiceops_agent.versions import CURRENT

EXTRACTION_JSON = {
    "vendor_name": "Acme",
    "invoice_number": "INV-1",
    "currency": "EUR",
    "total_amount": 99.5,
    "lines": [{"line_no": "1", "qty": 2, "unit_price": 49.75, "line_total": 99.5}],
    "confidences": {"vendor_name": 0.97, "total_amount": 0.93, "line[1].qty": 0.88},
}


class _ReplayStore:
    async def put(self, *a: object, **k: object) -> None: ...

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return key


@pytest.mark.integration
def test_agent_writes_ledger_entry_with_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        command.upgrade(Config("alembic.ini"), "head")

        async def scenario() -> dict[str, object]:
            engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
            factory = async_sessionmaker(engine, expire_on_commit=False)

            cassettes = CassetteStore(root=tmp_path / "c")
            cassettes.save("extract-vision", "it-clean", "h", json.dumps(EXTRACTION_JSON))
            gateway = GatewayClient(
                base_url="http://gateway.invalid",
                api_key="sk-test",
                cassette_store=cassettes,
                cassette_mode="replay",
            )
            agent = ExtractionAgent(store=_ReplayStore(), gateway=gateway, session_factory=factory)
            # Redirect the document fetch to skip HTTP (content is irrelevant in replay)
            agent._fetch_b64 = lambda doc_ref: _async_return("ZmFrZQ==")  # type: ignore[method-assign]

            async with factory() as session:
                invoice = Invoice(content_hash="h-extract-1", status="RECEIVED")
                session.add(invoice)
                await session.flush()
                run = Run(
                    invoice_id=invoice.invoice_id,
                    graph_version=CURRENT.graph,
                    model_versions={},
                    status="RUNNING",
                )
                session.add(run)
                await session.commit()
                invoice_id, run_id = invoice.invoice_id, run.run_id

            extraction = await agent.extract(
                "raw/h-extract-1",
                "application/pdf",
                run_id=run_id,
                invoice_id=invoice_id,
                scenario="it-clean",
            )

            async with factory() as session:
                entries = (await session.execute(select(LedgerEntry))).scalars().all()
            await engine.dispose()
            return {
                "is_typed": isinstance(extraction, InvoiceExtraction),
                "min_conf": extraction.min_confidence(),
                "entries": [
                    (
                        e.actor_type,
                        e.actor_id,
                        e.event.get("event"),
                        e.prompt_template_version,
                        (e.model_versions or {}).get("graph"),
                    )
                    for e in entries
                ],
            }

        result = asyncio.run(scenario())

    assert result["is_typed"] is True
    assert result["min_conf"] == 0.88
    assert result["entries"] == [
        (
            "AGENT",
            "extract",
            "extract.completed",
            "extract@v2",
            CURRENT.graph,
        )
    ]


async def _async_return(value: str) -> str:
    return value

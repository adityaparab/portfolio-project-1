"""Integration: dashboard summary over pipeline-produced data (#33)."""

import asyncio
import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.agents.extraction import ExtractionAgent
from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.data import seed_erp
from invoiceops_agent.data.erp import clean_invoice_for, generate
from invoiceops_agent.db.models import Invoice as InvoiceRow
from invoiceops_agent.db.models import Run as RunRow
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient
from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.runtime import NodeContext
from invoiceops_agent.tools.near_dup import HashEmbedder, NearDupService
from invoiceops_agent.tools.validation_config import TAX_RATES
from invoiceops_agent.versions import CURRENT

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DATASET = generate(seed=11, vendors=5, purchase_orders=15)


class _FakeStore:
    async def put(self, *a: object, **k: object) -> None: ...

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return key


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        monkeypatch.setenv("INVOICEOPS_ALEMBIC_DSN", dsn)
        command.upgrade(Config("alembic.ini"), "head")
        yield {"dsn": dsn, "tmp": tmp_path}


def _clean_pair() -> tuple[Any, Any]:
    vendors = {v.name: v for v in DATASET.vendors}
    for po in DATASET.purchase_orders:
        vendor = vendors[po.vendor_name]
        net = sum(line.line_total for line in po.lines)
        age = (DATASET.reference_date - po.ordered_at).days
        if po.status == "OPEN" and po.currency == "EUR" and net <= Decimal("2000") and age <= 90:
            return po, vendor
    raise AssertionError("no clean PO")


def _extraction(spec: Any, bump: Decimal | None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(json.dumps(dict(spec.extraction_dict())))
    if bump is not None:
        line: dict[str, Any] = data["lines"][0]
        old_total = Decimal(line["line_total"])
        line["unit_price"] = str(Decimal(line["unit_price"]) * (1 + bump))
        line["line_total"] = str(Decimal(line["unit_price"]) * Decimal(line["qty"]))
        delta = Decimal(line["line_total"]) - old_total
        data["total_amount"] = str(Decimal(data["total_amount"]) + delta)
        rate = TAX_RATES.get(line.get("tax_code") or "", Decimal("0"))
        data["tax_total"] = str(Decimal(data["tax_total"]) + delta * rate)
    data["confidences"] = {k: 0.98 for k in data if k not in ("lines", "confidences")}
    return data


def test_summary_reflects_processed_invoices(stack: dict[str, Any]) -> None:
    po, vendor = _clean_pair()
    clean = clean_invoice_for(
        po, vendor, invoice_number="INV-D-C", issue_date=DATASET.reference_date
    )
    mm = clean_invoice_for(po, vendor, invoice_number="INV-D-M", issue_date=DATASET.reference_date)

    async def main() -> dict[str, Any]:
        engine = create_async_engine(
            stack["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await seed_erp.seed_database(engine, DATASET)
        conns: list[Any] = []
        try:

            async def run_one(scenario: str, extraction: dict[str, Any], content_hash: str) -> None:
                async def fetch_b64(doc_ref: str) -> str:
                    return "ZmFrZQ=="

                cassettes = CassetteStore(root=stack["tmp"] / f"c-{scenario}")
                cassettes.save("extract-vision", scenario, "h", json.dumps(extraction))
                gateway = GatewayClient(
                    base_url="http://gateway.invalid",
                    api_key="sk-test",
                    cassette_store=cassettes,
                    cassette_mode="replay",
                )
                agent = ExtractionAgent(
                    store=_FakeStore(), gateway=gateway, session_factory=sessions
                )
                agent._fetch_b64 = fetch_b64  # type: ignore[method-assign]
                context = NodeContext(
                    sessions=sessions,
                    store=_FakeStore(),
                    gateway=gateway,
                    extraction_agent=agent,
                    near_dup=NearDupService(HashEmbedder()),
                    clock=lambda: FIXED_NOW,
                    gateway_scenario=scenario,
                )
                saver, conn = await open_saver(stack["dsn"])
                conns.append(conn)
                runner = GraphRunner(context, saver)
                async with sessions() as session:
                    invoice = InvoiceRow(content_hash=content_hash, doc_ref=f"raw/{content_hash}")
                    session.add(invoice)
                    await session.flush()
                    session.add(
                        RunRow(
                            invoice_id=invoice.invoice_id,
                            graph_version=CURRENT.graph,
                            model_versions={},
                            status="QUEUED",
                        )
                    )
                    await session.commit()
                    invoice_id = invoice.invoice_id
                await runner.run_invoice(invoice_id)

            await run_one("dash-clean", _extraction(clean, None), "d" * 63 + "c")
            await run_one("dash-mm", _extraction(mm, Decimal("0.10")), "d" * 63 + "m")

            app = create_app(Settings())
            transport = httpx.ASGITransport(app=app)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=transport, base_url="http://t") as client,
            ):
                summary = (await client.get("/v1/metrics/summary")).json()
            return summary
        finally:
            for conn in conns:
                with contextlib.suppress(Exception):
                    await conn.close()
            await engine.dispose()

    out = asyncio.run(main())
    assert out["invoices_processed"] == 2
    assert out["invoices_auto_approved"] == 1
    assert out["stp_rate"] == 0.5
    assert out["exceptions_open"] == 1
    assert out["exception_types"][0]["type"] == "PRICE_MM"
    assert set(out["aging"]) == {"on_track", "over_4h", "over_24h"}
    assert len(out["volume_by_day"]) == 1
    assert out["volume_by_day"][0]["total"] == 2
    assert out["volume_by_day"][0]["auto_approved"] == 1
    assert out["cost_per_invoice"] is None  # lands with #43
    assert out["p95_latency_seconds"] is None

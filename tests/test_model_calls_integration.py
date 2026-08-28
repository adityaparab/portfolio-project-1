"""Integration: model-call audit trail (reasoning + output persisted,
endpoint serves them; English-only prompts pinned)."""

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.data import seed_erp
from invoiceops_agent.data.erp import clean_invoice_for, generate
from invoiceops_agent.db.models import Invoice as InvoiceRow
from invoiceops_agent.db.models import ModelCall
from invoiceops_agent.db.models import Run as RunRow
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient
from invoiceops_agent.graph import runtime
from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.tools.validation_config import TAX_RATES
from invoiceops_agent.versions import CURRENT

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DATASET = generate(seed=11, vendors=5, purchase_orders=15)

TRIAGE_JSON = {
    "classification": "PRICE_MM",
    "confidence": 0.9,
    "suggested_action": "ESCALATE",
    "recommendation": "Unit price above PO; confirm the increase.",
    "rationale": "Beyond tolerance band.",
    "evidence_cited": ["PRICE_MM"],
}


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


def _inflated(spec: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(json.dumps(dict(spec.extraction_dict())))
    line: dict[str, Any] = data["lines"][0]
    old_total = Decimal(line["line_total"])
    line["unit_price"] = str(Decimal(line["unit_price"]) * Decimal("1.10"))
    line["line_total"] = str(Decimal(line["unit_price"]) * Decimal(line["qty"]))
    delta = Decimal(line["line_total"]) - old_total
    data["total_amount"] = str(Decimal(data["total_amount"]) + delta)
    rate = TAX_RATES.get(line.get("tax_code") or "", Decimal("0"))
    data["tax_total"] = str(Decimal(data["tax_total"]) + delta * rate)
    data["confidences"] = {k: 0.98 for k in data if k not in ("lines", "confidences")}
    return data


def test_model_calls_recorded_and_served(stack: dict[str, Any]) -> None:
    vendors = {v.name: v for v in DATASET.vendors}
    po = next(
        p
        for p in DATASET.purchase_orders
        if p.status == "OPEN"
        and p.currency == "EUR"
        and sum(line.line_total for line in p.lines) <= Decimal("2000")
        and (DATASET.reference_date - p.ordered_at).days <= 90
    )
    spec = clean_invoice_for(
        po, vendors[po.vendor_name], invoice_number="INV-MC", issue_date=DATASET.reference_date
    )

    async def main() -> dict[str, Any]:
        engine = create_async_engine(
            stack["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await seed_erp.seed_database(engine, DATASET)
        conns: list[Any] = []
        try:

            async def fetch_b64(doc_ref: str) -> str:
                return "ZmFrZQ=="

            cassettes = CassetteStore(root=stack["tmp"] / "c-mc")
            cassettes.save("extract-vision", "mc", "h", json.dumps(_inflated(spec)))
            cassettes.save("triage-reasoner", "mc", "h", json.dumps(TRIAGE_JSON))
            gateway = GatewayClient(
                base_url="http://gateway.invalid",
                api_key="sk-test",
                cassette_store=cassettes,
                cassette_mode="replay",
            )
            # build_context attaches the audit observer to the gateway
            context = runtime.build_context(sessions=sessions, store=_FakeStore(), gateway=gateway)
            context.gateway_scenario = "mc"
            context.extraction_agent._fetch_b64 = fetch_b64  # type: ignore[method-assign]
            saver, conn = await open_saver(stack["dsn"])
            conns.append(conn)
            runner = GraphRunner(context, saver)
            async with sessions() as session:
                invoice = InvoiceRow(content_hash="m" * 64, doc_ref="raw/m")
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

            async with sessions() as session:
                rows = (
                    (await session.execute(select(ModelCall).order_by(ModelCall.call_id)))
                    .scalars()
                    .all()
                )
                recorded = [
                    {
                        "stage": r.stage,
                        "alias": r.alias,
                        "wire_model": r.wire_model,
                        "prompt_version": r.prompt_version,
                        "reasoning": r.reasoning_text,
                        "output_head": r.output_text[:40],
                        "run_id": r.run_id,
                    }
                    for r in rows
                ]

            app = create_app(Settings())
            transport = httpx.ASGITransport(app=app)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=transport, base_url="http://t") as client,
            ):
                calls = (await client.get("/v1/runs/1/model-calls")).json()
                trace = (await client.get("/v1/runs/1/trace")).json()
            return {"recorded": recorded, "calls": calls, "trace": trace, "invoice_id": invoice_id}
        finally:
            for conn in conns:
                with contextlib.suppress(Exception):
                    await conn.close()
            await engine.dispose()

    out = asyncio.run(main())

    # DB rows: extract + triage stages recorded with output + versions
    stages = {row["stage"]: row for row in out["recorded"]}
    assert "extract" in stages and "triage" in stages
    extract_row = stages["extract"]
    assert extract_row["alias"] == "extract-vision"
    assert extract_row["prompt_version"] == "extract@v3"  # English-only version
    assert extract_row["run_id"] == 1
    assert extract_row["output_head"].lstrip().startswith("{")  # the model JSON
    assert stages["triage"]["prompt_version"] == "triage@v2"
    assert stages["triage"]["output_head"].lstrip().startswith("{")

    # Endpoint serves the same audit material
    by_stage = {c["stage"]: c for c in out["calls"]}
    assert by_stage["extract"]["wire_model"] == "extract-vision"
    assert "reasoning_text" in by_stage["extract"]
    assert by_stage["triage"]["alias"] == "triage-reasoner"

    # Trace carries the live-stage metadata + prompt versions
    assert out["trace"]["stage_models"]["extract"]["alias"] == "extract-vision"
    assert out["trace"]["stage_models"]["prompt_versions"]["extract"] == "extract@v3"
    assert out["trace"]["active_node"] is None  # paused run: active is the pause edge

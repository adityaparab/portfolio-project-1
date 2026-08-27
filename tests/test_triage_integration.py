"""Integration: triage agent wired into exception_triage (issue #30 ACs) —
recommendation persisted on the exception, AGENT ledger entry with version
pins, degraded basic package when the agent is unavailable."""

import asyncio
import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.agents.extraction import ExtractionAgent
from invoiceops_agent.agents.triage import TriageAgent
from invoiceops_agent.data import seed_erp
from invoiceops_agent.data.erp import clean_invoice_for, generate
from invoiceops_agent.db.models import ExceptionRecord, Invoice, LedgerEntry
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

TRIAGE_PAYLOAD = {
    "classification": "PRICE_MM",
    "confidence": 0.92,
    "suggested_action": "ESCALATE",
    "recommendation": "Unit price is 10% above the PO; confirm the increase was agreed.",
    "rationale": "PRICE_MM finding exceeds the 2% tolerance band.",
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
        command.upgrade(Config("alembic.ini"), "head")
        yield {"dsn": dsn, "tmp": tmp_path}


def _inflated_extraction(spec: Any) -> dict[str, Any]:
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


def _clean_pair() -> tuple[Any, Any]:
    vendors = {v.name: v for v in DATASET.vendors}
    for po in DATASET.purchase_orders:
        vendor = vendors[po.vendor_name]
        net = sum(line.line_total for line in po.lines)
        age = (DATASET.reference_date - po.ordered_at).days
        if po.status == "OPEN" and po.currency == "EUR" and net <= Decimal("2000") and age <= 90:
            return po, vendor
    raise AssertionError("no clean PO")


async def _run_pipeline(
    env: dict[str, Any],
    scenario: str,
    extraction: dict[str, Any],
    triage_payload: dict[str, Any] | None = None,
    *,
    triage_broken: bool = False,
) -> int:
    async def fetch_b64(doc_ref: str) -> str:
        return "ZmFrZQ=="

    cassettes = CassetteStore(root=env["tmp"] / f"c-{scenario}")
    cassettes.save("extract-vision", scenario, "h", json.dumps(extraction))
    if triage_payload is not None:
        cassettes.save("triage-reasoner", scenario, "h", json.dumps(triage_payload))
    gateway = GatewayClient(
        base_url="http://gateway.invalid",
        api_key="sk-test",
        cassette_store=cassettes,
        cassette_mode="replay",
    )
    agent = ExtractionAgent(store=_FakeStore(), gateway=gateway, session_factory=env["sessions"])
    agent._fetch_b64 = fetch_b64  # type: ignore[method-assign]
    context = NodeContext(
        sessions=env["sessions"],
        store=_FakeStore(),
        gateway=gateway,
        extraction_agent=agent,
        triage_agent=(
            TriageAgent(gateway) if (triage_payload is not None or triage_broken) else None
        ),
        near_dup=NearDupService(HashEmbedder()),
        clock=lambda: FIXED_NOW,
        gateway_scenario=scenario,
    )
    saver, conn = await open_saver(env["dsn"])
    env["conns"].append(conn)
    runner = GraphRunner(context, saver)
    async with env["sessions"]() as session:
        from invoiceops_agent.db.models import Invoice as Inv
        from invoiceops_agent.db.models import Run as RunRow

        invoice = Inv(content_hash=(scenario[:1] + "t") * 32, doc_ref=f"raw/{scenario}")
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
    return invoice_id


def test_triage_recommendation_persisted_with_version_pins(stack: dict[str, Any]) -> None:
    po, vendor = _clean_pair()
    spec = clean_invoice_for(
        po, vendor, invoice_number="INV-TRIAGE", issue_date=DATASET.reference_date
    )

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        invoice_id = await _run_pipeline(
            env, "triage-ok", _inflated_extraction(spec), TRIAGE_PAYLOAD
        )
        async with env["sessions"]() as session:
            record = (
                await session.execute(
                    select(ExceptionRecord).where(ExceptionRecord.invoice_id == invoice_id)
                )
            ).scalar_one()
            entries = (
                (
                    await session.execute(
                        select(LedgerEntry)
                        .where(LedgerEntry.invoice_id == invoice_id)
                        .order_by(LedgerEntry.seq)
                    )
                )
                .scalars()
                .all()
            )
            invoice = await session.get(Invoice, invoice_id)
            assert invoice is not None
            return {
                "type": record.type,
                "recommendation": record.recommendation,
                "events": [
                    (e.event.get("event"), e.actor_type, e.prompt_template_version) for e in entries
                ],
                "invoice_status": invoice.status,
            }

    async def main() -> dict[str, Any]:
        engine = create_async_engine(
            stack["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        env = {
            "sessions": async_sessionmaker(engine, expire_on_commit=False),
            "conns": [],
            **stack,
        }
        try:
            await seed_erp.seed_database(engine, DATASET)
            return await case(env)
        finally:
            for conn in env["conns"]:
                with contextlib.suppress(Exception):
                    await conn.close()
            await engine.dispose()

    out = asyncio.run(main())
    assert out["type"] == "PRICE_MM"
    rec = out["recommendation"]
    assert rec is not None
    assert rec["classification"] == "PRICE_MM"  # agrees with the deterministic code
    assert rec["abstained"] is False
    assert "10%" in rec["recommendation"]
    assert rec["prompt_version"] == "triage@v1"

    events = out["events"]
    triage_entries = [e for e in events if e[0] == "triage.completed"]
    assert len(triage_entries) == 1
    assert triage_entries[0][1] == "AGENT"  # actor type
    assert triage_entries[0][2] == "triage@v1"  # prompt version pinned
    assert out["invoice_status"] == "EXCEPTION"


def test_degraded_basic_package_when_agent_unavailable(stack: dict[str, Any]) -> None:
    """No triage cassette => the gateway call fails => the exception still
    opens with the deterministic package and a recorded degrade reason."""
    po, vendor = _clean_pair()
    spec = clean_invoice_for(
        po, vendor, invoice_number="INV-DEGRADE", issue_date=DATASET.reference_date
    )

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        # agent configured, but no triage cassette -> gateway call fails ->
        # basic package with the degrade reason recorded
        invoice_id = await _run_pipeline(
            env, "degrade", _inflated_extraction(spec), None, triage_broken=True
        )
        async with env["sessions"]() as session:
            record = (
                await session.execute(
                    select(ExceptionRecord).where(ExceptionRecord.invoice_id == invoice_id)
                )
            ).scalar_one()
            opened = (
                (
                    await session.execute(
                        select(LedgerEntry).where(
                            LedgerEntry.invoice_id == invoice_id,
                            LedgerEntry.actor_id == "exception_triage",
                        )
                    )
                )
                .scalars()
                .one()
            )
            return {
                "type": record.type,
                "recommendation": record.recommendation,
                "triage_meta": opened.event.get("triage"),
            }

    async def main() -> dict[str, Any]:
        engine = create_async_engine(
            stack["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        env = {"sessions": async_sessionmaker(engine, expire_on_commit=False), "conns": [], **stack}
        try:
            await seed_erp.seed_database(engine, DATASET)
            return await case(env)
        finally:
            for conn in env["conns"]:
                with contextlib.suppress(Exception):
                    await conn.close()
            await engine.dispose()

    out = asyncio.run(main())
    assert out["type"] == "PRICE_MM"  # deterministic package intact
    assert out["recommendation"] is None
    assert out["triage_meta"]["classification"] is None
    assert "unavailable" in out["triage_meta"]["reason"]

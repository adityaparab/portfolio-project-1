"""Integration: trace + provenance endpoints (issue #35 ACs) — a full run's
history reconstructible from these two endpoints alone."""

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

from invoiceops_agent.agents.extraction import ExtractionAgent
from invoiceops_agent.agents.triage import TriageAgent
from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.data import seed_erp
from invoiceops_agent.data.erp import clean_invoice_for, generate
from invoiceops_agent.db.models import ExceptionRecord
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
AUDIT = {"X-IO-User": "priya@invoiceops", "X-IO-Role": "audit"}
ANALYST = {"X-IO-User": "maria@invoiceops", "X-IO-Role": "analyst"}
TRIAGE = {
    "classification": "PRICE_MM",
    "confidence": 0.9,
    "suggested_action": "ESCALATE",
    "recommendation": "Confirm the 10% increase was agreed.",
    "rationale": "Beyond the 2% band.",
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


def test_full_history_reconstructible_from_two_endpoints(stack: dict[str, Any]) -> None:
    async def main() -> dict[str, Any]:
        engine = create_async_engine(
            stack["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await seed_erp.seed_database(engine, DATASET)
        conns: list[Any] = []
        try:
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
                po,
                vendors[po.vendor_name],
                invoice_number="INV-PROV",
                issue_date=DATASET.reference_date,
            )

            async def fetch_b64(doc_ref: str) -> str:
                return "ZmFrZQ=="

            cassettes = CassetteStore(root=stack["tmp"] / "c-prov")
            cassettes.save("extract-vision", "prov", "h", json.dumps(_inflated(spec)))
            cassettes.save("triage-reasoner", "prov", "h", json.dumps(TRIAGE))
            gateway = GatewayClient(
                base_url="http://gateway.invalid",
                api_key="sk-test",
                cassette_store=cassettes,
                cassette_mode="replay",
            )
            agent = ExtractionAgent(store=_FakeStore(), gateway=gateway, session_factory=sessions)
            agent._fetch_b64 = fetch_b64  # type: ignore[method-assign]
            context = NodeContext(
                sessions=sessions,
                store=_FakeStore(),
                gateway=gateway,
                extraction_agent=agent,
                triage_agent=TriageAgent(gateway),
                near_dup=NearDupService(HashEmbedder()),
                clock=lambda: FIXED_NOW,
                gateway_scenario="prov",
            )
            saver, conn = await open_saver(stack["dsn"])
            conns.append(conn)
            runner = GraphRunner(context, saver)
            async with sessions() as session:
                invoice = InvoiceRow(content_hash="p" * 64, doc_ref="raw/p")
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
            await runner.run_invoice(invoice_id)  # exception path, paused

            async with sessions() as session:
                exc = (
                    await session.execute(
                        select(ExceptionRecord).where(ExceptionRecord.invoice_id == invoice_id)
                    )
                ).scalar_one()
                exception_id = exc.exception_id
                run_id = exc.run_id

            app = create_app(Settings())
            transport = httpx.ASGITransport(app=app)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=transport, base_url="http://t") as client,
            ):
                trace = (await client.get(f"/v1/runs/{run_id}/trace", headers=AUDIT)).json()
                prov = (
                    await client.get(f"/v1/invoices/{invoice_id}/provenance", headers=AUDIT)
                ).json()
                analyst_denied = await client.get(
                    f"/v1/invoices/{invoice_id}/provenance", headers=ANALYST
                )
                missing = await client.get("/v1/runs/999999/trace", headers=AUDIT)
                # Maria escalates (claims the exception), Dan approves (four-eyes ok)
                await client.post(
                    f"/v1/exceptions/{exception_id}/decision",
                    json={
                        "action": "ESCALATE",
                        "rationale": "Price delta needs a manager call.",
                        "reason_code": "OVER_BAND",
                        "escalate_to": "manager-queue",
                    },
                    headers=ANALYST,
                )
                await client.post(
                    f"/v1/exceptions/{exception_id}/decision",
                    json={
                        "action": "APPROVE",
                        "rationale": "Agreed price increase confirmed with vendor.",
                        "reason_code": "PRICE_TOLERATED",
                    },
                    headers={"X-IO-User": "dan@invoiceops", "X-IO-Role": "manager"},
                )
                prov_after = (
                    await client.get(f"/v1/invoices/{invoice_id}/provenance", headers=AUDIT)
                ).json()
                trace_after = (await client.get(f"/v1/runs/{run_id}/trace", headers=AUDIT)).json()
            return {
                "trace": trace,
                "prov": prov,
                "analyst_status": analyst_denied.status_code,
                "missing_status": missing.status_code,
                "prov_after": prov_after,
                "trace_after": trace_after,
                "exception_id": exception_id,
            }
        finally:
            for conn in conns:
                with contextlib.suppress(Exception):
                    await conn.close()
            await engine.dispose()

    out = asyncio.run(main())

    # --- trace: complete node chain with the pause visible
    trace = out["trace"]
    assert trace["status"] == "AWAITING_DECISION"
    assert trace["node_trace"][-1] == "exception_triage"
    events = [e["event"] for e in trace["timeline"]]
    assert events[:6] == [
        "run.started",
        "extract.completed",
        "validate.completed",
        "match.completed",
        "exception.opened",
        "triage.completed",
    ]
    # version pins ride on the timeline
    triage_entry = next(e for e in trace["timeline"] if e["event"] == "triage.completed")
    assert triage_entry["prompt_template_version"] == "triage@v2"
    assert triage_entry["actor_type"] == "AGENT"

    # --- provenance before the decision
    prov = out["prov"]
    assert len(prov["runs"]) == 1
    assert prov["runs"][0]["graph_version"] == CURRENT.graph
    assert prov["exceptions"][0]["type"] == "PRICE_MM"
    assert prov["exceptions"][0]["recommendation"]["classification"] == "PRICE_MM"
    assert prov["decisions"] == []

    # --- RBAC + 404
    assert out["analyst_status"] == 403
    assert out["missing_status"] == 404

    # --- after the decision: full history from the two endpoints alone
    after = out["prov_after"]
    assert [d["action"] for d in after["decisions"]] == ["ESCALATE", "APPROVE"]
    decision = after["decisions"][-1]
    assert decision["actor_user"] == "dan@invoiceops"
    assert decision["action"] == "APPROVE"
    ledger_events = [e["event"] for e in after["ledger"]]
    assert "decision.recorded" in ledger_events
    assert "human_review.completed" in ledger_events
    assert "run.archived" in ledger_events
    assert after["exceptions"][0]["status"] == "RESOLVED"

    trace_after = out["trace_after"]
    assert trace_after["status"] == "COMPLETED"
    assert trace_after["node_trace"][-1] == "archive"
    post_decision = [e for e in trace_after["timeline"] if e["event"] == "decision.recorded"]
    assert post_decision[0]["actor_type"] == "HUMAN"
    actions = [e["payload"]["action"] for e in post_decision]
    assert actions == ["ESCALATE", "APPROVE"]

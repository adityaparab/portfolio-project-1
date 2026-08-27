"""Integration: decision endpoint + four-eyes + graph resume (issue #29 ACs).
Real Postgres, real pipeline (cassettes), one event loop per test."""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Iterator
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
from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.data import seed_erp
from invoiceops_agent.data.erp import clean_invoice_for, generate
from invoiceops_agent.db.models import Decision, ExceptionRecord, Invoice, LedgerEntry, Run
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient
from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.runtime import NodeContext
from invoiceops_agent.tools.near_dup import HashEmbedder, NearDupService
from invoiceops_agent.versions import CURRENT

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DATASET = generate(seed=11, vendors=5, purchase_orders=15)
MARIA = {"X-IO-User": "maria@invoiceops", "X-IO-Role": "analyst"}
DAN = {"X-IO-User": "dan@invoiceops", "X-IO-Role": "manager"}


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


async def _process_exception_invoice(env: dict[str, Any], scenario: str) -> int:
    """Drive one PRICE_MM invoice through the pipeline; returns invoice_id."""
    vendors = {v.name: v for v in DATASET.vendors}
    po = next(
        p
        for p in DATASET.purchase_orders
        if p.status == "OPEN"
        and p.currency == "EUR"
        and sum(line.line_total for line in p.lines) <= Decimal("2000")
        and (DATASET.reference_date - p.ordered_at).days <= 90
    )
    vendor = vendors[po.vendor_name]
    spec = clean_invoice_for(
        po, vendor, invoice_number=f"INV-{scenario}", issue_date=DATASET.reference_date
    )
    from invoiceops_agent.tools.validation_config import TAX_RATES

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

    async def fetch_b64(doc_ref: str) -> str:
        return "ZmFrZQ=="

    cassettes = CassetteStore(root=env["tmp"] / f"c-{scenario}")
    cassettes.save("extract-vision", scenario, "h", json.dumps(data))
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

        invoice = Inv(content_hash=(scenario[:1] * 64), doc_ref=f"raw/{scenario}")
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
    await runner.run_invoice(invoice_id)  # pauses at exception_triage
    return invoice_id


RunnerCase = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def run_case(stack: dict[str, Any], case: RunnerCase) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        engine = create_async_engine(
            stack["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await seed_erp.seed_database(engine, DATASET)
        conns: list[Any] = []
        try:
            return await case({**stack, "engine": engine, "sessions": sessions, "conns": conns})
        finally:
            for conn in conns:
                with contextlib.suppress(Exception):
                    await conn.close()
            await engine.dispose()

    return asyncio.run(main())


BODY = {
    "action": "APPROVE",
    "rationale": "Verified against the PO; price change was agreed in writing.",
    "reason_code": "PRICE_TOLERATED",
}


def test_four_eyes_idempotency_and_graph_resume(stack: dict[str, Any]) -> None:
    async def case(env: dict[str, Any]) -> dict[str, Any]:
        invoice_id = await _process_exception_invoice(env, "dec1")
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            async with env["sessions"]() as session:
                exc = (
                    await session.execute(
                        select(ExceptionRecord).where(ExceptionRecord.invoice_id == invoice_id)
                    )
                ).scalar_one()
                exception_id = exc.exception_id

            # Maria works + tries to approve her own exception: four-eyes 409
            maria_first = await client.post(
                f"/v1/exceptions/{exception_id}/decision", json=BODY, headers=MARIA
            )
            # Dan approves: 201, graph resumes
            dan = await client.post(
                f"/v1/exceptions/{exception_id}/decision", json=BODY, headers=DAN
            )
            dan_body = dan.json()
            # Dan double-submits the same decision: idempotent replay (200)
            dan_again = await client.post(
                f"/v1/exceptions/{exception_id}/decision", json=BODY, headers=DAN
            )
            # Someone else now tries a DIFFERENT decision: conflict 409
            other = await client.post(
                f"/v1/exceptions/{exception_id}/decision",
                json={**BODY, "action": "RETURN_TO_VENDOR", "reason_code": "WRONG_PRICE"},
                headers={"X-IO-User": "priya@invoiceops", "X-IO-Role": "audit"},
            )

        async with env["sessions"]() as session:
            decisions = (
                (
                    await session.execute(
                        select(Decision).where(Decision.exception_id == exception_id)
                    )
                )
                .scalars()
                .all()
            )
            invoice = await session.get(Invoice, invoice_id)
            run = (
                (
                    await session.execute(
                        select(Run).where(Run.invoice_id == invoice_id).order_by(Run.run_id.desc())
                    )
                )
                .scalars()
                .first()
            )
            ledger = [
                e.event.get("event")
                for e in (
                    await session.execute(
                        select(LedgerEntry)
                        .where(LedgerEntry.invoice_id == invoice_id)
                        .order_by(LedgerEntry.seq)
                    )
                )
                .scalars()
                .all()
            ]
            assert invoice is not None and run is not None
            return {
                "maria_status": maria_first.status_code,
                "maria_detail": maria_first.json()["extra"],  # RFC 7807 extension
                "dan_status": dan.status_code,
                "dan_body": dan_body,
                "replay_status": dan_again.status_code,
                "replay_flag": dan_again.headers.get("X-Idempotent-Replay"),
                "replay_body": dan_again.json(),
                "conflict_status": other.status_code,
                "decision_rows": [(d.actor_user, d.action) for d in decisions],
                "invoice_status": invoice.status,
                "run_status": run.status,
                "run_route": run.route,
                "ledger": ledger,
            }

    out = run_case(stack, case)

    # four-eyes: Maria claimed (assignee) and cannot approve
    assert out["maria_status"] == 409
    assert out["maria_detail"]["kind"] == "FOUR_EYES"

    # Dan approves: one decision row, resume happened
    assert out["dan_status"] == 201
    assert out["dan_body"]["graph_resumed"] is True
    assert out["decision_rows"] == [("dan@invoiceops", "APPROVE")]  # Maria's never wrote

    # idempotent double-submit
    assert out["replay_status"] == 200
    assert out["replay_flag"] == "true"
    assert out["replay_body"]["decision_id"] == out["dan_body"]["decision_id"]

    # conflicting decision after resolution
    assert out["conflict_status"] == 409

    # state transitions + audit
    assert out["invoice_status"] == "DECISION_APPROVED"
    assert out["run_status"] == "COMPLETED"  # resumed through archive
    assert out["run_route"] == "EXCEPTION"
    assert "decision.recorded" in out["ledger"]
    assert "human_review.completed" in out["ledger"]
    assert out["ledger"].count("run.archived") == 1


def test_escalate_keeps_open_and_404(stack: dict[str, Any]) -> None:
    async def case(env: dict[str, Any]) -> dict[str, Any]:
        invoice_id = await _process_exception_invoice(env, "dec2")
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            async with env["sessions"]() as session:
                exc = (
                    await session.execute(
                        select(ExceptionRecord).where(ExceptionRecord.invoice_id == invoice_id)
                    )
                ).scalar_one()
                exception_id = exc.exception_id
            escalate = await client.post(
                f"/v1/exceptions/{exception_id}/decision",
                json={
                    "action": "ESCALATE",
                    "rationale": "Amount band requires director sign-off.",
                    "reason_code": "OVER_LIMIT",
                    "escalate_to": "director-queue",
                },
                headers=MARIA,
            )
            missing = await client.post("/v1/exceptions/999999/decision", json=BODY, headers=DAN)

        async with env["sessions"]() as session:
            record = await session.get(ExceptionRecord, exception_id)
            assert record is not None
            run = (
                (
                    await session.execute(
                        select(Run).where(Run.invoice_id == invoice_id).order_by(Run.run_id.desc())
                    )
                )
                .scalars()
                .first()
            )
            assert run is not None
            return {
                "escalate_status": escalate.status_code,
                "escalate_body": escalate.json(),
                "missing_status": missing.status_code,
                "exc_status": record.status,
                "exc_assignee": record.assignee,
                "run_status": run.status,  # still paused — no resume on escalate
            }

    out = run_case(stack, case)
    assert out["escalate_status"] == 201
    assert out["escalate_body"]["graph_resumed"] is False
    assert out["missing_status"] == 404
    assert out["exc_status"] == "OPEN"
    assert out["exc_assignee"] == "director-queue"
    assert out["run_status"] == "AWAITING_DECISION"

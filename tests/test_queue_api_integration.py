"""Integration: queue + detail aggregate endpoints (issue #28 ACs) against a
real Postgres with pipeline-produced data. One event loop per test."""

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
from sqlalchemy import event
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


def _extraction_json(
    spec: Any, *, conf: float = 0.98, price_bump: Decimal | None = None
) -> dict[str, Any]:
    from invoiceops_agent.tools.validation_config import TAX_RATES

    data: dict[str, Any] = json.loads(json.dumps(dict(spec.extraction_dict())))
    if price_bump is not None:
        line: dict[str, Any] = data["lines"][0]
        old_total = Decimal(line["line_total"])
        qty = Decimal(line["qty"])
        line["unit_price"] = str(Decimal(line["unit_price"]) * (1 + price_bump))
        line["line_total"] = str(Decimal(line["unit_price"]) * qty)
        delta = Decimal(line["line_total"]) - old_total
        data["total_amount"] = str(Decimal(data["total_amount"]) + delta)
        rate = TAX_RATES.get(line.get("tax_code") or "", Decimal("0"))
        data["tax_total"] = str(Decimal(data["tax_total"]) + delta * rate)
    fields = [k for k in data if k not in ("lines", "confidences")]
    data["confidences"] = {k: conf for k in fields}
    for line in data["lines"]:
        for field in ("qty", "unit_price", "line_total"):
            data["confidences"][f"line[{line['line_no']}].{field}"] = conf
    return data


def _clean_pair() -> tuple[Any, Any]:
    vendors = {v.name: v for v in DATASET.vendors}
    for po in DATASET.purchase_orders:
        vendor = vendors[po.vendor_name]
        net = sum(line.line_total for line in po.lines)
        age = (DATASET.reference_date - po.ordered_at).days
        if po.status == "OPEN" and po.currency == "EUR" and net <= Decimal("2000") and age <= 90:
            return po, vendor
    raise AssertionError("no clean PO in dataset")


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


async def _process(
    env: dict[str, Any], scenario: str, extraction: dict[str, Any], content_hash: str
) -> int:
    async def fetch_b64(doc_ref: str) -> str:
        return "ZmFrZQ=="

    cassettes = CassetteStore(root=env["tmp"] / f"c-{scenario}")
    cassettes.save("extract-vision", scenario, "h", json.dumps(extraction))
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
        invoice = InvoiceRow(content_hash=content_hash, doc_ref=f"raw/{content_hash}")
        session.add(invoice)
        await session.flush()
        run = RunRow(
            invoice_id=invoice.invoice_id,
            graph_version=CURRENT.graph,
            model_versions={},
            status="QUEUED",
        )
        session.add(run)
        await session.commit()
        invoice_id = invoice.invoice_id
    await runner.run_invoice(invoice_id)
    return invoice_id


def test_queue_and_detail_with_rbac(stack: dict[str, Any]) -> None:
    po, vendor = _clean_pair()
    clean_spec = clean_invoice_for(
        po, vendor, invoice_number="INV-Q-CLEAN", issue_date=DATASET.reference_date
    )
    mm_spec = clean_invoice_for(
        po, vendor, invoice_number="INV-Q-MM", issue_date=DATASET.reference_date
    )

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        clean_id = await _process(env, "q-clean", _extraction_json(clean_spec), "1" * 64)
        mm_id = await _process(
            env, "q-mm", _extraction_json(mm_spec, price_bump=Decimal("0.10")), "2" * 64
        )

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)

        query_count = 0

        async with app.router.lifespan_context(app):

            @event.listens_for(app.state.engine.sync_engine, "before_cursor_execute")
            def _count(*args: Any) -> None:
                nonlocal query_count
                query_count += 1

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                page = (await client.get("/v1/invoices", headers=_persona("analyst"))).json()
                auto_only = (
                    await client.get(
                        "/v1/invoices", params={"route": "AUTO"}, headers=_persona("analyst")
                    )
                ).json()
                exceptions_only = (
                    await client.get(
                        "/v1/invoices",
                        params={"exception_type": "PRICE_MM", "sort": "sla_due_at"},
                        headers=_persona("analyst"),
                    )
                ).json()
                paged = (
                    await client.get(
                        "/v1/invoices",
                        params={"limit": 1, "offset": 0},
                        headers=_persona("analyst"),
                    )
                ).json()
                missing = await client.get("/v1/invoices/999999", headers=_persona("analyst"))

                query_count = 0
                detail_analyst = (
                    await client.get(f"/v1/invoices/{mm_id}", headers=_persona("analyst"))
                ).json()
                detail_queries = query_count
                detail_audit = (
                    await client.get(f"/v1/invoices/{mm_id}", headers=_persona("audit"))
                ).json()
                detail_clean = (
                    await client.get(f"/v1/invoices/{clean_id}", headers=_persona("manager"))
                ).json()

        return {
            "page": page,
            "auto_only": auto_only,
            "exceptions_only": exceptions_only,
            "paged": paged,
            "missing_status": missing.status_code,
            "detail_analyst": detail_analyst,
            "detail_audit": detail_audit,
            "detail_clean": detail_clean,
            "detail_queries": detail_queries,
            "mm_id": mm_id,
            "clean_id": clean_id,
        }

    out = run_case(stack, case)

    # queue: both invoices, denormalized fields populated, newest first
    assert out["page"]["total"] == 2
    assert out["page"]["limit"] == 25 and out["page"]["offset"] == 0
    statuses = {item["invoice_id"]: item["status"] for item in out["page"]["items"]}
    assert statuses == {out["mm_id"]: "EXCEPTION", out["clean_id"]: "AUTO_APPROVED"}
    item = next(i for i in out["page"]["items"] if i["invoice_id"] == out["mm_id"])
    assert item["invoice_number"] == "INV-Q-MM"
    assert item["currency"] == "EUR"
    assert item["amount_total"] is not None
    assert item["exception"]["type"] == "PRICE_MM"
    assert item["exception"]["sla_overdue_seconds"] is not None  # fixed clock vs. wall now
    assert item["run"]["route"] == "EXCEPTION"

    # server-side filters + pagination
    assert out["auto_only"]["total"] == 1
    assert out["auto_only"]["items"][0]["invoice_id"] == out["clean_id"]
    assert out["exceptions_only"]["total"] == 1
    assert out["exceptions_only"]["items"][0]["exception"]["type"] == "PRICE_MM"
    assert out["paged"]["total"] == 2 and len(out["paged"]["items"]) == 1

    assert out["missing_status"] == 404

    # detail: one round trip — fixed query count (invoice+join, ledger, count)
    assert out["detail_queries"] == 3, "detail aggregate must not N+1"
    analyst = out["detail_analyst"]
    assert analyst["state_available"] is True
    assert analyst["invoice"]["exception"]["type"] == "PRICE_MM"
    assert analyst["extraction"]["po_number"] == po.po_number
    assert analyst["extraction"]["min_confidence"] is not None
    assert analyst["match"]["outcome"] == "MISMATCH"
    assert any(f["code"] == "PRICE_MM" for f in analyst["match"]["findings"])
    assert analyst["gate"] is None  # mismatches route around the gate
    assert analyst["ledger"]["entry_count"] == 6  # started..policy + exception + archive

    # RBAC: analyst gets no version pins; audit does (provenance fields)
    analyst_entry = analyst["ledger"]["last_entries"][0]
    audit_entry = out["detail_audit"]["ledger"]["last_entries"][0]
    assert analyst_entry["versions"] is None
    assert analyst_entry["policy_version"] is None
    assert audit_entry["versions"] is not None or audit_entry["policy_version"] is not None

    # clean invoice aggregate: AUTO path, no exception block
    clean = out["detail_clean"]
    assert clean["invoice"]["status"] == "AUTO_APPROVED"
    assert clean["invoice"]["run"]["route"] == "AUTO"
    assert clean["exception"] is None
    assert clean["gate"]["tau"] == 0.85  # gate ran on the AUTO path
    assert clean["ledger"]["entry_count"] == 8


def _persona(role: str) -> dict[str, str]:
    return {"X-IO-User": f"{role}@invoiceops", "X-IO-Role": role}

"""E2E integration: the wired pipeline against real Postgres + pgvector
checkpoints (issue #25 ACs). Deterministic: gateway on cassettes, hashing
embedder, fixed clock, patched document fetch. Requires Docker.

All async work in each test happens inside ONE ``asyncio.run`` — asyncpg
connections bind to the event loop that created them."""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Iterator
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
from invoiceops_agent.data import seed_erp
from invoiceops_agent.data.erp import CleanInvoiceSpec, clean_invoice_for, generate
from invoiceops_agent.db.models import ExceptionRecord, Invoice, LedgerEntry, Run
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient
from invoiceops_agent.gateway_client.errors import GatewayTransportError
from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.runtime import NodeContext
from invoiceops_agent.tools.near_dup import HashEmbedder, NearDupService
from invoiceops_agent.tools.validation_config import TAX_RATES
from invoiceops_agent.versions import CURRENT

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DATASET = generate(seed=7, vendors=6, purchase_orders=18)  # deterministic


class _FakeStore:
    async def put(self, *a: object, **k: object) -> None: ...

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return key


class _FlakyGateway:
    """Delegates to the real gateway but fails the first N complete() calls
    with a transport error (infra-flake simulation for the resume AC)."""

    def __init__(self, inner: GatewayClient, fail_first: int) -> None:
        self._inner = inner
        self._fail_first = fail_first

    async def complete(self, *a: object, **k: object) -> object:
        if self._fail_first > 0:
            self._fail_first -= 1
            raise GatewayTransportError("simulated infra flake", alias="extract-vision")
        return await self._inner.complete(*a, **k)  # type: ignore[arg-type]


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        command.upgrade(Config("alembic.ini"), "head")
        yield {"dsn": dsn, "tmp": tmp_path}


# ------------------------------------------------------------ one-loop helpers


async def setup_env(stack: dict[str, Any]) -> dict[str, Any]:
    """Migrated DB is up; create engine + sessions + seeded ERP (same loop)."""
    engine = create_async_engine(stack["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await seed_erp.seed_database(engine, DATASET)
    return {"engine": engine, "sessions": sessions, "conns": [], "scenario": None, **stack}


async def teardown_env(env: dict[str, Any]) -> None:
    for conn in env["conns"]:
        with contextlib.suppress(Exception):
            await conn.close()
    await env["engine"].dispose()


async def make_runner(env: dict[str, Any], gateway: GatewayClient) -> GraphRunner:
    async def fetch_b64(doc_ref: str) -> str:  # patched doc fetch — no HTTP
        return "ZmFrZQ=="

    agent = ExtractionAgent(store=_FakeStore(), gateway=gateway, session_factory=env["sessions"])
    agent._fetch_b64 = fetch_b64  # type: ignore[method-assign]
    context = NodeContext(
        sessions=env["sessions"],
        store=_FakeStore(),
        gateway=gateway,
        extraction_agent=agent,
        near_dup=NearDupService(HashEmbedder()),
        clock=lambda: FIXED_NOW,
        gateway_scenario=env["scenario"],
    )
    saver, conn = await open_saver(env["dsn"])
    env["conns"].append(conn)
    return GraphRunner(context, saver)


def make_gateway(tmp: Path, scenario: str, extraction_json: dict[str, Any]) -> GatewayClient:
    cassettes = CassetteStore(root=tmp / f"cass-{scenario}")
    cassettes.save("extract-vision", scenario, "h", json.dumps(extraction_json))
    return GatewayClient(
        base_url="http://gateway.invalid",
        api_key="sk-test",
        cassette_store=cassettes,
        cassette_mode="replay",
    )


async def create_invoice_run(env: dict[str, Any], content_hash: str) -> tuple[int, int]:
    from invoiceops_agent.db.models import Invoice as Inv
    from invoiceops_agent.db.models import Run as RunRow

    async with env["sessions"]() as session:
        invoice = Inv(content_hash=content_hash, doc_ref=f"raw/{content_hash}")
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
        return invoice.invoice_id, run.run_id


async def ledger_events(env: dict[str, Any], run_id: int) -> list[str]:
    async with env["sessions"]() as session:
        rows = (
            (
                await session.execute(
                    select(LedgerEntry)
                    .where(LedgerEntry.run_id == run_id)
                    .order_by(LedgerEntry.seq)
                )
            )
            .scalars()
            .all()
        )
        return [r.event.get("event", "?") for r in rows]


# ------------------------------------------------------------ extraction shapes


def extraction_json(spec: CleanInvoiceSpec, *, conf: float = 0.98) -> dict[str, Any]:
    data: dict[str, Any] = dict(spec.extraction_dict())
    data["confidences"] = {
        "vendor_name": conf,
        "invoice_number": conf,
        "po_number": conf,
        "issue_date": conf,
        "currency": conf,
        "total_amount": conf,
        "tax_total": conf,
        "iban": conf,
        **{
            f"line[{line['line_no']}].{field}": conf
            for line in data["lines"]
            for field in ("qty", "unit_price", "line_total")
        },
    }
    return data


def bump_price(extraction: dict[str, Any], pct: Decimal) -> dict[str, Any]:
    """Raise line 1's unit price by pct, recomputing totals so only the
    PRICE check fires (not MATH/TAX)."""
    out: dict[str, Any] = json.loads(json.dumps(extraction))
    line: dict[str, Any] = out["lines"][0]
    old_price = Decimal(line["unit_price"])
    old_total = Decimal(line["line_total"])
    qty = Decimal(line["qty"])
    line["unit_price"] = str(old_price * (1 + pct))
    line["line_total"] = str(Decimal(line["unit_price"]) * qty)
    delta = Decimal(line["line_total"]) - old_total
    out["total_amount"] = str(Decimal(out["total_amount"]) + delta)
    rate = TAX_RATES.get(line.get("tax_code") or "", Decimal("0"))
    out["tax_total"] = str(Decimal(out["tax_total"]) + delta * rate)
    return out


def clean_target() -> tuple[Any, Any]:
    """A PO that yields a policy-clean invoice: OPEN, fresh, EUR, under limit."""
    vendors = {v.name: v for v in DATASET.vendors}
    for po in DATASET.purchase_orders:
        vendor = vendors[po.vendor_name]
        net = sum(line.line_total for line in po.lines)
        age = (DATASET.reference_date - po.ordered_at).days
        if po.status == "OPEN" and po.currency == "EUR" and net <= Decimal("2000") and age <= 90:
            return po, vendor
    raise AssertionError("seed dataset (seed=7) lacks a policy-clean PO")


EXPECTED_LEDGER = [
    "run.started",
    "extract.completed",
    "validate.completed",
    "match.completed",
    "policy.evaluated",
    "gate.decided",
    "invoice.auto_approved",
    "run.archived",
]

RunnerCase = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def run_case(stack: dict[str, Any], case: RunnerCase) -> dict[str, Any]:
    """One event loop for setup + case + teardown; returns plain data."""

    async def main() -> dict[str, Any]:
        env = await setup_env(stack)
        try:
            return await case(env)
        finally:
            await teardown_env(env)

    return asyncio.run(main())


# ---------------------------------------------------------------------- tests


def test_clean_seeded_invoice_auto_archives(stack: dict[str, Any]) -> None:
    po, vendor = clean_target()
    spec = clean_invoice_for(
        po, vendor, invoice_number="INV-E2E-CLEAN", issue_date=DATASET.reference_date
    )

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        env["scenario"] = "e2e-clean"
        runner = await make_runner(
            env, make_gateway(env["tmp"], "e2e-clean", extraction_json(spec))
        )
        invoice_id, run_id = await create_invoice_run(env, "e" * 64)
        final = await runner.run_invoice(invoice_id)
        again = await runner.run_invoice(invoice_id)  # idempotent replay
        async with env["sessions"]() as session:
            invoice = await session.get(Invoice, invoice_id)
            run = await session.get(Run, run_id)
            assert invoice is not None and run is not None
            db = {
                "invoice_status": invoice.status,
                "run_status": run.status,
                "route": run.route,
                "confidence": float(run.confidence or 0),
            }
        return {
            "final_route": final.route.value if final.route else None,
            "trace_tail": final.node_trace[-1],
            "replay_route": again.route.value if again.route else None,
            "ledger": await ledger_events(env, run_id),
            **db,
        }

    out = run_case(stack, case)
    assert out["final_route"] == "AUTO"
    assert out["trace_tail"] == "archive"
    assert out["replay_route"] == "AUTO"
    assert out["invoice_status"] == "AUTO_APPROVED"
    assert out["run_status"] == "COMPLETED"
    assert out["route"] == "AUTO"
    assert out["confidence"] >= 0.85
    assert out["ledger"] == EXPECTED_LEDGER  # exactly one entry per node


def test_price_mismatch_routes_to_exception_with_findings(stack: dict[str, Any]) -> None:
    po, vendor = clean_target()
    spec = clean_invoice_for(
        po, vendor, invoice_number="INV-E2E-MM", issue_date=DATASET.reference_date
    )
    inflated = bump_price(extraction_json(spec), Decimal("0.10"))  # +10% > 2% band

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        env["scenario"] = "e2e-mm"
        runner = await make_runner(env, make_gateway(env["tmp"], "e2e-mm", inflated))
        invoice_id, _run_id = await create_invoice_run(env, "f" * 64)
        final = await runner.run_invoice(invoice_id)
        async with env["sessions"]() as session:
            invoice = await session.get(Invoice, invoice_id)
            record = (
                await session.execute(
                    select(ExceptionRecord).where(ExceptionRecord.invoice_id == invoice_id)
                )
            ).scalar_one()
            assert invoice is not None
            return {
                "route": final.route.value if final.route else None,
                "exception_code": final.exception["code"] if final.exception else None,
                "invoice_status": invoice.status,
                "exception_type": record.type,
                "severity": record.severity,
                "status": record.status,
                "sla_due_at": record.sla_due_at is not None,
                "finding_codes": [f["code"] for f in record.evidence["findings"]],
                "delta_po": next(
                    (
                        f["delta"]["po"]
                        for f in record.evidence["findings"]
                        if f["code"] == "PRICE_MM"
                    ),
                    None,
                ),
            }

    out = run_case(stack, case)
    assert out["route"] == "EXCEPTION"
    assert out["exception_code"] == "PRICE_MM"
    assert out["invoice_status"] == "EXCEPTION"
    assert out["exception_type"] == "PRICE_MM"
    assert out["severity"] == "HIGH"
    assert out["status"] == "OPEN"
    assert out["sla_due_at"]
    assert "PRICE_MM" in out["finding_codes"]
    assert out["delta_po"] == str(po.lines[0].unit_price)


def test_low_confidence_escalates_as_approval_required(stack: dict[str, Any]) -> None:
    po, vendor = clean_target()
    spec = clean_invoice_for(
        po, vendor, invoice_number="INV-E2E-LOWCONF", issue_date=DATASET.reference_date
    )
    weak = extraction_json(spec, conf=0.42)  # gate term collapses below tau

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        env["scenario"] = "e2e-lowconf"
        runner = await make_runner(env, make_gateway(env["tmp"], "e2e-lowconf", weak))
        invoice_id, _run_id = await create_invoice_run(env, "0" * 64)
        final = await runner.run_invoice(invoice_id)
        return {
            "route": final.route.value if final.route else None,
            "code": final.exception["code"] if final.exception else None,
            "confidence": final.confidence,
        }

    out = run_case(stack, case)
    assert out["route"] == "EXCEPTION"
    assert out["code"] == "APPROVAL_REQUIRED"
    assert out["confidence"] is not None and out["confidence"] < 0.85


def test_crashed_run_resumes_from_checkpoint_exactly_once(stack: dict[str, Any]) -> None:
    po, vendor = clean_target()
    spec = clean_invoice_for(
        po, vendor, invoice_number="INV-E2E-RESUME", issue_date=DATASET.reference_date
    )

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        env["scenario"] = "e2e-resume"
        gateway = make_gateway(env["tmp"], "e2e-resume", extraction_json(spec))
        runner = await make_runner(env, gateway)
        runner._ctx.extraction_agent._gateway = _FlakyGateway(  # type: ignore[assignment]
            gateway, fail_first=1
        )
        invoice_id, run_id = await create_invoice_run(env, "1" * 64)

        crashed = False
        try:
            await runner.run_invoice(invoice_id)
        except GatewayTransportError:
            crashed = True
        ledger_after_crash = await ledger_events(env, run_id)

        recovered = await runner.run_invoice(invoice_id)  # resumes from checkpoint
        return {
            "crashed": crashed,
            "ledger_after_crash": ledger_after_crash,
            "route": recovered.route.value if recovered.route else None,
            "ledger": await ledger_events(env, run_id),
        }

    out = run_case(stack, case)
    assert out["crashed"]
    assert out["ledger_after_crash"] == ["run.started"]  # ingest ran, extract did not
    assert out["route"] == "AUTO"
    # Exactly-once: ingest did not re-run; the tail ran once.
    assert out["ledger"] == EXPECTED_LEDGER
    assert out["ledger"].count("run.started") == 1

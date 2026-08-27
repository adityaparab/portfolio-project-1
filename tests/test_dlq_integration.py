"""Integration: retries + DLQ record/replay against real Postgres (issue #27
ACs). Same determinism disciplines as the pipeline E2E tests. Docker required."""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
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
from invoiceops_agent.data.erp import clean_invoice_for, generate
from invoiceops_agent.db.models import DLQEntry, LedgerEntry, Run
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient
from invoiceops_agent.gateway_client.errors import GatewayTransportError
from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.dlq import DLQService, ReplayError
from invoiceops_agent.graph.retries import RetryPolicy
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.runtime import NodeContext
from invoiceops_agent.tools.near_dup import HashEmbedder, NearDupService
from invoiceops_agent.versions import CURRENT

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DATASET = generate(seed=7, vendors=6, purchase_orders=18)
FAST_POLICY = RetryPolicy(attempts=3, base_delay=0.01, max_delay=0.01, jitter=0.0)


class _FakeStore:
    async def put(self, *a: object, **k: object) -> None: ...

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return key


class _FlakyGateway:
    """Transport-flake injector: fails the first N (or all) complete() calls."""

    def __init__(self, inner: GatewayClient, fail_first: int = 0, *, always: bool = False) -> None:
        self._inner = inner
        self._remaining = fail_first
        self._always = always

    async def complete(self, *a: object, **k: object) -> object:
        if self._always or self._remaining > 0:
            self._remaining -= 1
            raise GatewayTransportError("simulated outage", alias="extract-vision")
        return await self._inner.complete(*a, **k)  # type: ignore[arg-type]


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        command.upgrade(Config("alembic.ini"), "head")
        yield {"dsn": dsn, "tmp": tmp_path}


async def setup_env(stack: dict[str, Any]) -> dict[str, Any]:
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
    async def fetch_b64(doc_ref: str) -> str:
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
    return GraphRunner(context, saver, retry_policy=FAST_POLICY)


def make_gateway(tmp: Path, scenario: str, content: str) -> GatewayClient:
    cassettes = CassetteStore(root=tmp / f"cass-{scenario}")
    cassettes.save("extract-vision", scenario, "h", content)
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


def clean_spec() -> Any:
    vendors = {v.name: v for v in DATASET.vendors}
    for po in DATASET.purchase_orders:
        vendor = vendors[po.vendor_name]
        from decimal import Decimal

        net = sum(line.line_total for line in po.lines)
        age = (DATASET.reference_date - po.ordered_at).days
        if po.status == "OPEN" and po.currency == "EUR" and net <= Decimal("2000") and age <= 90:
            return clean_invoice_for(
                po, vendor, invoice_number="INV-DLQ", issue_date=DATASET.reference_date
            )
    raise AssertionError("no clean PO in dataset")


def extraction_content(spec: Any) -> str:
    data = dict(spec.extraction_dict())
    data["confidences"] = {k: 0.98 for k in (*data.keys(), "po_number")} | {
        f"line[{line['line_no']}].{f}": 0.98
        for line in data["lines"]
        for f in ("qty", "unit_price", "line_total")
    }
    return json.dumps(data)


RunnerCase = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def run_case(stack: dict[str, Any], case: RunnerCase) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        env = await setup_env(stack)
        try:
            return await case(env)
        finally:
            await teardown_env(env)

    return asyncio.run(main())


def test_infra_flake_survived_without_human_action(stack: dict[str, Any]) -> None:
    spec = clean_spec()

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        env["scenario"] = "flake"
        gateway = make_gateway(env["tmp"], "flake", extraction_content(spec))
        runner = await make_runner(env, gateway)
        runner._ctx.extraction_agent._gateway = _FlakyGateway(  # type: ignore[assignment]
            gateway, fail_first=1
        )
        invoice_id, run_id = await create_invoice_run(env, "a" * 64)
        final = await runner.run_invoice(invoice_id)
        async with env["sessions"]() as session:
            dlq_count = len((await session.execute(select(DLQEntry))).scalars().all())
        return {
            "route": final.route.value if final.route else None,
            "dlq": dlq_count,
            "ledger": await ledger_events(env, run_id),
        }

    out = run_case(stack, case)
    assert out["route"] == "AUTO"  # survived the flake in-run
    assert out["dlq"] == 0  # no human action, no DLQ entry
    assert out["ledger"].count("run.started") == 1
    assert "run.failed" not in out["ledger"]


def test_business_failure_never_retries_and_lands_in_dlq(stack: dict[str, Any]) -> None:
    async def case(env: dict[str, Any]) -> dict[str, Any]:
        env["scenario"] = "garbage"
        runner = await make_runner(env, make_gateway(env["tmp"], "garbage", "utterly not json"))
        invoice_id, run_id = await create_invoice_run(env, "b" * 64)
        crashed = False
        try:
            await runner.run_invoice(invoice_id)
        except Exception:
            crashed = True
        async with env["sessions"]() as session:
            entry = (await session.execute(select(DLQEntry))).scalars().one()
            run = await session.get(Run, run_id)
            assert run is not None
            return {
                "crashed": crashed,
                "kind": entry.failure_kind,
                "attempts": entry.attempts,
                "node": entry.node,
                "run_status": run.status,
                "snapshot_has_invoice": bool(entry.state_snapshot.get("invoice_id")),
                "ledger": await ledger_events(env, run_id),
            }

    out = run_case(stack, case)
    assert out["crashed"]
    assert out["kind"] == "BUSINESS"
    assert out["attempts"] == 1  # deterministic: zero node retries
    assert out["node"] == "extract"
    assert out["run_status"] == "FAILED"
    assert out["snapshot_has_invoice"]
    assert "run.failed" in out["ledger"]


def test_exhausted_infra_lands_in_dlq_and_replays_exactly_once(
    stack: dict[str, Any],
) -> None:
    spec = clean_spec()

    async def case(env: dict[str, Any]) -> dict[str, Any]:
        env["scenario"] = "outage"
        gateway = make_gateway(env["tmp"], "outage", extraction_content(spec))
        runner = await make_runner(env, gateway)
        runner._ctx.extraction_agent._gateway = _FlakyGateway(  # type: ignore[assignment]
            gateway, always=True
        )
        invoice_id, run_id = await create_invoice_run(env, "c" * 64)

        crashed = False
        try:
            await runner.run_invoice(invoice_id)
        except Exception:
            crashed = True

        async with env["sessions"]() as session:
            entry = (await session.execute(select(DLQEntry))).scalars().one()
            failed = {
                "crashed": crashed,
                "kind": entry.failure_kind,
                "attempts": entry.attempts,
                "status_after_failure": entry.status,
            }
            dlq_id = entry.dlq_id

        # Replay with a healthy gateway: same checkpointer thread resumes.
        healthy_runner = await make_runner(env, gateway)
        result = await DLQService().replay(env["sessions"], healthy_runner, dlq_id)

        async with env["sessions"]() as session:
            entry = await session.get(DLQEntry, dlq_id)
            assert entry is not None
            # double replay is refused (idempotency guard)
            refused = False
            try:
                await DLQService().replay(env["sessions"], healthy_runner, dlq_id)
            except ReplayError:
                refused = True
        return {
            **failed,
            "replay_route": result["route"],
            "dlq_status": entry.status,
            "refused_double_replay": refused,
            "ledger": await ledger_events(env, run_id),
        }

    out = run_case(stack, case)
    assert out["crashed"]
    assert out["kind"] == "INFRA"
    assert out["attempts"] == 3  # the retry budget, then DLQ
    assert out["status_after_failure"] == "PENDING"
    assert out["replay_route"] == "AUTO"  # replay completed the run
    assert out["dlq_status"] == "REPLAYED"
    assert out["refused_double_replay"]
    ledger = out["ledger"]
    # Exactly-once: ingest ran once across failure + replay; the tail ran once.
    assert ledger.count("run.started") == 1
    assert ledger.count("extract.completed") == 1
    assert ledger.count("invoice.auto_approved") == 1
    assert "run.failed" in ledger and "dlq.replayed" in ledger

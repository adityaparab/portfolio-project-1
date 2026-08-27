"""Integration test: typed ledger writer/reader (issue #14, ADR 0004).

Requires Docker. Round-trip on real Postgres, version pins, per-run seq
monotonicity, pagination, and append-only invariants intact.
"""

import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.db.models import Invoice, Run
from invoiceops_agent.ledger.api import (
    ActorType,
    LedgerAppend,
    reader,
    writer,
)
from invoiceops_agent.versions import CURRENT, VersionPins


@pytest.mark.integration
def test_ledger_writer_reader_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        command.upgrade(Config("alembic.ini"), "head")

        async def scenario() -> dict[str, Any]:
            engine = create_async_engine(
                dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
            )
            factory = async_sessionmaker(engine, expire_on_commit=False)

            async with factory() as session:
                invoice = Invoice(content_hash="h-ledger-1", status="RECEIVED")
                session.add(invoice)
                await session.flush()
                run = Run(
                    invoice_id=invoice.invoice_id,
                    graph_version=CURRENT.graph,
                    model_versions={},
                    status="QUEUED",
                )
                session.add(run)
                await session.flush()

                e1 = await writer.append(
                    session,
                    LedgerAppend(
                        actor_type=ActorType.SYSTEM,
                        actor_id="ingest",
                        run_id=run.run_id,
                        invoice_id=invoice.invoice_id,
                        event={"event": "ingest.accepted"},
                    ),
                )
                e2 = await writer.append(
                    session,
                    LedgerAppend(
                        actor_type=ActorType.AGENT,
                        actor_id="extract",
                        run_id=run.run_id,
                        invoice_id=invoice.invoice_id,
                        event={"event": "extract.completed"},
                        prompt_version="extract@v3",
                        versions=VersionPins(
                            graph="0.2.0", models={"extract-vision": "glm-ocr"}
                        ),
                    ),
                )
                e3 = await writer.append(
                    session,
                    LedgerAppend(
                        actor_type=ActorType.POLICY,
                        actor_id="policy-engine",
                        run_id=run.run_id,
                        invoice_id=invoice.invoice_id,
                        event={"event": "policy.evaluated"},
                        policy_version="policy@v2",
                    ),
                )
                await session.commit()

                by_run = await reader.by_run(session, run.run_id)
                page1 = await reader.by_run(session, run.run_id, limit=2, offset=0)
                page2 = await reader.by_run(session, run.run_id, limit=2, offset=2)
                by_invoice = await reader.by_invoice(session, invoice.invoice_id)
                rows = (
                    (await session.execute(select(Invoice.invoice_id))).scalars().all()
                )

            await engine.dispose()
            return {
                "seqs": [e1.seq, e2.seq, e3.seq],
                "by_run": [(v.actor_type.value, v.seq) for v in by_run],
                "page1": [v.seq for v in page1],
                "page2": [v.seq for v in page2],
                "by_invoice_n": len(by_invoice),
                "agent_pins": by_run[1].versions,
                "agent_prompt": by_run[1].prompt_template_version,
                "policy_pin": by_run[2].policy_version,
                "system_pins": by_run[0].versions,
                "invoices": len(rows),
                "created_iso": by_run[0].created_at,
            }

        result = asyncio.run(scenario())

    assert result["seqs"] == [1, 2, 3]  # per-run monotonic
    assert [t for t, _ in result["by_run"]] == ["SYSTEM", "AGENT", "POLICY"]
    assert result["page1"] == [1, 2] and result["page2"] == [3]
    assert result["by_invoice_n"] == 3
    assert result["system_pins"] == {"graph": CURRENT.graph}  # default pins applied
    assert result["agent_pins"] == {"extract-vision": "glm-ocr", "graph": "0.2.0"}
    assert result["agent_prompt"] == "extract@v3"
    assert result["policy_pin"] == "policy@v2"
    assert isinstance(result["created_iso"], str) and "T" in str(result["created_iso"])
    assert result["invoices"] == 1

"""Integration test: append-only enforcement on ledger/decisions (issue #7, ADR 0004).

Requires Docker (testcontainers). Runs migrations as owner, then proves
INSERT works while UPDATE/DELETE are rejected by the trigger guards.
"""

import asyncio

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_append_only_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        command.upgrade(Config("alembic.ini"), "head")

        async def scenario() -> None:
            conn = await asyncpg.connect(dsn=dsn)
            try:
                await conn.execute(
                    "INSERT INTO vendors (name, bank_details, risk_flags, is_active, created_at) "
                    "VALUES ('Test Vendor', '{}', '[]', true, now())"
                )
                invoice = await conn.fetchval(
                    "INSERT INTO invoices (content_hash, status, created_at) "
                    "VALUES ('hash-1', 'RECEIVED', now()) RETURNING invoice_id"
                )
                run = await conn.fetchval(
                    "INSERT INTO runs (invoice_id, graph_version, model_versions, "
                    "status, started_at) "
                    f"VALUES ({invoice}, 'v0', '{{}}', 'RUNNING', now()) RETURNING run_id"
                )
                entry = await conn.fetchval(
                    "INSERT INTO ledger (run_id, invoice_id, seq, actor_type, event, created_at) "
                    f"VALUES ({run}, {invoice}, 1, 'SYSTEM', '{{\"event\": \"ingest\"}}', now()) "
                    "RETURNING entry_id"
                )
                assert entry is not None

                with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                    await conn.execute(f"UPDATE ledger SET seq = 99 WHERE entry_id = {entry}")
                with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                    await conn.execute(f"DELETE FROM ledger WHERE entry_id = {entry}")

                exc = await conn.fetchval(
                    "INSERT INTO exceptions (invoice_id, run_id, type, severity, "
                    "evidence, status, created_at) "
                    f"VALUES ({invoice}, {run}, 'PRICE_MM', 'MEDIUM', '{{}}', 'OPEN', now()) "
                    "RETURNING exception_id"
                )
                decision = await conn.fetchval(
                    "INSERT INTO decisions (exception_id, actor_user, action, "
                    "rationale, reason_code, created_at) "
                    f"VALUES ({exc}, 'maria', 'APPROVE', 'ok', 'MATCHED', now()) "
                    "RETURNING decision_id"
                )
                assert decision is not None
                with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                    await conn.execute(f"DELETE FROM decisions WHERE decision_id = {decision}")

                # Non-guarded tables remain fully mutable for the owner.
                await conn.execute(
                    f"UPDATE invoices SET status = 'PROCESSED' WHERE invoice_id = {invoice}"
                )
            finally:
                await conn.close()

        asyncio.run(scenario())


@pytest.mark.integration
def test_append_only_guards_reversible(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "822d4351d71a")  # drop guards
        command.upgrade(cfg, "head")  # re-apply

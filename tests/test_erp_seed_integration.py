"""Integration test: ERP seeder against a real Postgres (issue #20 AC).
Requires Docker (testcontainers)."""

import asyncio
from collections.abc import Iterator

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.data import seed_erp
from invoiceops_agent.data.erp import generate

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated_dsn(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)
        command.upgrade(Config("alembic.ini"), "head")
        yield dsn


def test_seed_populates_clean_db_deterministically(migrated_dsn: str) -> None:
    async def run() -> tuple[dict[str, int], list[str], list[str], int]:
        engine = create_async_engine(
            migrated_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        try:
            dataset = generate(seed=42, vendors=8, purchase_orders=20)
            counts = await seed_erp.seed_database(engine, dataset)

            conn = await asyncpg.connect(dsn=migrated_dsn)
            try:
                names: list[str] = [
                    r["name"]
                    for r in await conn.fetch("SELECT name FROM vendors ORDER BY vendor_id")
                ]
                ibans: list[str] = [
                    r["iban"]
                    for r in await conn.fetch(
                        "SELECT bank_details->>'iban' AS iban FROM vendors ORDER BY vendor_id"
                    )
                ]
                ledger: int = await conn.fetchval(
                    "SELECT count(*) FROM ledger WHERE event->>'event' = 'erp.seeded'"
                )
            finally:
                await conn.close()
            return counts, names, ibans, ledger
        finally:
            await engine.dispose()

    counts, names, ibans, ledger_entries = asyncio.run(run())
    assert counts == {"vendors": 8, "purchase_orders": 20, "goods_receipts": 20}
    assert ledger_entries == 1  # seed recorded in the append-only audit trail
    assert all(iban.startswith("DE") and len(iban) == 22 for iban in ibans)

    # Regeneration is bit-identical for the same seed (issue AC): the rows in
    # Postgres match a fresh in-memory generate() of the same inputs.
    dataset = generate(seed=42, vendors=8, purchase_orders=20)
    assert [v.name for v in dataset.vendors] == names


def test_reseed_on_dirty_db_aborts_loudly(migrated_dsn: str) -> None:
    async def run() -> None:
        engine = create_async_engine(
            migrated_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        try:
            dataset = generate(seed=1, vendors=3, purchase_orders=5)
            await seed_erp.seed_database(engine, dataset)
            with pytest.raises(seed_erp.SeedError, match="refusing to mix"):
                await seed_erp.seed_database(engine, dataset)
        finally:
            await engine.dispose()

    asyncio.run(run())

"""Integration test: full upgrade → downgrade → upgrade cycle on a clean
pgvector Postgres container (ARCHITECTURE §9 / issue #6 AC). Requires Docker."""

import asyncio

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_migration_cycle_on_clean_pgvector_db(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        url = pg.get_connection_url()
        dsn = url.replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", dsn)

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")  # clean DB → full schema
        command.downgrade(cfg, "base")  # fully reversible
        command.upgrade(cfg, "head")  # and re-applicable

        async def verify() -> tuple[str | None, str | None]:
            conn = await asyncpg.connect(dsn=dsn)
            try:
                ext = await conn.fetchval(
                    "SELECT extname FROM pg_extension WHERE extname = 'vector'"
                )
                idx = await conn.fetchval(
                    "SELECT indexname FROM pg_indexes WHERE indexname = 'ix_invoices_embedding'"
                )
                return ext, idx
            finally:
                await conn.close()

        ext, idx = asyncio.run(verify())
        assert ext == "vector"  # pgvector extension genuinely installed
        assert idx == "ix_invoices_embedding"  # HNSW near-dup index present

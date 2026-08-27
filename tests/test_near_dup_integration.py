"""Integration tests: near-duplicate detection on real Postgres + pgvector
(issue #23 ACs). Requires Docker (testcontainers)."""

import asyncio
import math
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.db.models import EMBEDDING_DIM, Invoice
from invoiceops_agent.tools.near_dup import (
    HashEmbedder,
    NearDupHit,
    NearDupService,
    salient_text,
)

pytestmark = pytest.mark.integration


def invoice_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "vendor_name": "Acme Supplies GmbH",
        "invoice_number": "INV-2026-0042",
        "issue_date": "2026-08-14",
        "currency": "EUR",
        "total_amount": Decimal("149.99"),
        "tax_total": Decimal("28.50"),
        "iban": "DE02120300000000202051",
        "lines": [
            {
                "description": "Widget Pro 500",
                "qty": Decimal("3"),
                "unit_price": Decimal("50.00"),
                "line_total": Decimal("150.00"),
            }
        ],
    }
    base.update(overrides)
    return base


DISTINCT = invoice_dict(
    vendor_name="Nordic Parts Oy",
    invoice_number="INV-2026-0999",
    total_amount=Decimal("8910.00"),
    lines=[
        {
            "description": "Industrial pump",
            "qty": Decimal("2"),
            "unit_price": Decimal("4455.00"),
            "line_total": Decimal("8910.00"),
        }
    ],
)


@pytest.fixture
def dsn(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", url)
        command.upgrade(Config("alembic.ini"), "head")
        yield url


def _engine(dsn: str) -> Any:
    return create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))


async def _add_invoice(session: AsyncSession, content_hash: str) -> Invoice:
    row = Invoice(content_hash=content_hash, status="RECEIVED")
    session.add(row)
    await session.flush()
    return row


def test_altered_duplicate_flagged_distinct_invoice_not(dsn: str) -> None:
    async def run() -> tuple[tuple[NearDupHit, ...], tuple[NearDupHit, ...], Invoice | None]:
        engine = _engine(dsn)
        embedder = HashEmbedder()
        service = NearDupService(embedder)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            original = await _add_invoice(session, "a" * 64)
            await service.store_embedding(
                session, original.invoice_id, await embedder.embed(salient_text(invoice_dict()))
            )
            distinct = await _add_invoice(session, "b" * 64)
            await service.store_embedding(
                session, distinct.invoice_id, await embedder.embed(salient_text(DISTINCT))
            )
            await session.commit()

            altered = await _add_invoice(session, "c" * 64)
            altered_outcome = await service.check_and_store(
                session,
                altered.invoice_id,
                salient_text(invoice_dict(total_amount=Decimal("150.49"))),  # +0.33% edit
            )

            # The distinct invoice must not match the original (self excluded):
            distinct_vector = await embedder.embed(salient_text(DISTINCT))
            distinct_hits = await service.find_similar(
                session, invoice_id=distinct.invoice_id, vector=distinct_vector
            )

            stored = await session.get(Invoice, altered.invoice_id)
            assert stored is not None
            await session.refresh(stored)  # UPDATE stmts don't sync the identity map
            rows = (altered_outcome.hits, tuple(distinct_hits), stored)
        await engine.dispose()
        return rows

    altered_hits, distinct_hits, stored = asyncio.run(run())
    assert len(altered_hits) == 1  # the original — not the distinct invoice
    assert altered_hits[0].similarity >= 0.90
    assert not distinct_hits  # distinct invoice finds nothing similar
    assert stored is not None and stored.embedding is not None  # own vector persisted


def test_threshold_boundary_is_strict(dsn: str) -> None:
    """Below threshold excluded, above included. Margins of 1e-3, not exact:
    pgvector stores float4, so a vector constructed for cosine == threshold
    quantizes astride it — exact-boundary assertions are meaningless."""

    async def run() -> list[Any]:
        engine = _engine(dsn)
        service = NearDupService(HashEmbedder(), threshold=0.9)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        probe = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
        margin = 1e-3
        below = 0.9 - margin
        above = 0.9 + margin
        below_vec = [below, math.sqrt(1 - below**2)] + [0.0] * (EMBEDDING_DIM - 2)
        above_vec = [above, math.sqrt(1 - above**2)] + [0.0] * (EMBEDDING_DIM - 2)

        async with sessions() as session:
            for i, vec in enumerate((below_vec, above_vec)):
                row = await _add_invoice(session, f"{i}" * 64)
                await service.store_embedding(session, row.invoice_id, list(vec))
            await session.commit()
            hits = await service.find_similar(session, invoice_id=999_999, vector=probe)
        await engine.dispose()
        return hits

    hits = asyncio.run(run())
    sims = {round(h.similarity, 6) for h in hits}
    assert all(s > 0.9 for s in sims)  # below-threshold row excluded
    assert any(s > 0.9 for s in sims)  # above-threshold row included


def test_similarity_query_uses_hnsw_index_no_seq_scan(dsn: str) -> None:
    async def run() -> str:
        engine = _engine(dsn)
        embedder = HashEmbedder()
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            for i in range(1200):
                session.add(
                    Invoice(
                        content_hash=f"{i:064x}",
                        embedding=await embedder.embed(f"invoice {i} shared vendor words"),
                    )
                )
            await session.commit()
        await engine.dispose()

        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("ANALYZE invoices")
            vec = ",".join("0.5" for _ in range(EMBEDDING_DIM))
            plan_rows = await conn.fetch(
                "EXPLAIN (COSTS OFF) SELECT invoice_id FROM invoices "
                f"WHERE embedding IS NOT NULL ORDER BY embedding <=> '[{vec}]'::vector LIMIT 5"
            )
            return "\n".join(str(r[0]) for r in plan_rows)
        finally:
            await conn.close()

    plan = asyncio.run(run())
    assert "Seq Scan" not in plan, f"similarity query must use the index, got: {plan}"
    assert "index scan using ix_invoices_embedding" in plan.lower()

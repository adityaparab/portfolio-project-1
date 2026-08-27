"""Integration test: POST /v1/invoices against real Postgres + MinIO (issue #11).

Requires Docker. Runs migrations, boots the app with real dependencies, and
verifies storage + rows + ledger + idempotency + duplicate handling.
"""

import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.api.deps import build_object_store
from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.services.ingest import IngestService
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.db.models import Invoice, LedgerEntry, Run
from invoiceops_agent.storage.minio import MinioObjectStore

TOKEN = {"Authorization": "Bearer test-token"}


def _pdf(payload: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"upload": ("inv.pdf", payload, "application/pdf")}


@pytest.mark.integration
def test_ingest_full_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    with (
        PostgresContainer("pgvector/pgvector:pg16") as pg,
        MinioContainer("minio/minio:RELEASE.2025-01-20T14-49-07Z") as mc,
    ):
        pg_dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
        monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", pg_dsn)
        command.upgrade(Config("alembic.ini"), "head")

        async def scenario() -> tuple[dict[str, Any], int, int]:
            settings = Settings(
                database_dsn=pg_dsn,
                service_token="test-token",
                minio_base_url=f"http://{mc.get_config()['endpoint']}",
                minio_access_key=mc.get_config()["access_key"],
                minio_secret_key=mc.get_config()["secret_key"],
            )
            engine = create_async_engine(
                pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
            )
            factory = async_sessionmaker(engine, expire_on_commit=False)
            store: MinioObjectStore = build_object_store(settings)  # type: ignore[assignment]

            app: FastAPI = create_app(settings)
            app.state.ingest_service = IngestService(store, factory, settings)

            async with (
                AsyncClient(
                    transport=ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://test",
                ) as client,
                engine.connect() as _,
            ):
                # Original carries the idempotency key; replays return it verbatim.
                ok = await client.post(
                    "/v1/invoices",
                    files=_pdf(b"%PDF-1.4 integration-1"),
                    headers={**TOKEN, "Idempotency-Key": "idem-1"},
                )
                replay = await client.post(
                    "/v1/invoices",
                    files=_pdf(b"%PDF-1.4 integration-1"),
                    headers={**TOKEN, "Idempotency-Key": "idem-1"},
                )
                replay_again = await client.post(
                    "/v1/invoices",
                    files=_pdf(b"%PDF-1.4 integration-1"),
                    headers={**TOKEN, "Idempotency-Key": "idem-1"},
                )
                dup = await client.post(  # same bytes, NO key → 200 duplicate (#13)
                    "/v1/invoices", files=_pdf(b"%PDF-1.4 integration-1"), headers=TOKEN
                )
                # Concurrent race: same NEW content from two requests at once.
                import asyncio as _aio

                r1, r2 = await _aio.gather(
                    client.post("/v1/invoices", files=_pdf(b"%PDF-1.4 race-A"), headers=TOKEN),
                    client.post("/v1/invoices", files=_pdf(b"%PDF-1.4 race-A"), headers=TOKEN),
                )

                async with factory() as session:
                    invoices = (await session.execute(select(Invoice))).scalars().all()
                    runs = (await session.execute(select(Run))).scalars().all()
                    ledger = (await session.execute(select(LedgerEntry))).scalars().all()
                    stored = await store.presigned_get(
                        f"raw/{invoices[0].content_hash}", expires_seconds=5
                    )

            await engine.dispose()
            return (
                {
                    "ok": ok.status_code,
                    "replay": replay.status_code,
                    "replay_again": replay_again.status_code,
                    "replay_same": replay_again.json() == replay.json(),
                    "dup": dup.status_code,
                    "dup_detail": dup.json(),
                    "race_codes": sorted([r1.status_code, r2.status_code]),
                    "race_same_invoice": r1.json()["invoice_id"] == r2.json()["invoice_id"],
                    "invoices": len(invoices),
                    "runs": len(runs),
                    "run_states": sorted((r.status, r.route) for r in runs),
                    "ledger": len(ledger),
                    "ledger_events": sorted(e.event.get("event") for e in ledger),
                    "doc_ref": invoices[0].doc_ref,
                    "presigned": bool(stored),
                },
                invoices[0].invoice_id,
                ledger[0].entry_id,
            )

        result, invoice_id, entry_id = asyncio.run(scenario())

    assert result["ok"] == 201
    body = result["dup_detail"]
    assert result["dup"] == 200  # duplicate: 200 + original ref, not 409 (#13)
    assert body["invoice_id"] == invoice_id
    assert body["duplicate"] is True
    assert body["status"] == "REJECTED"
    assert result["replay"] == 201 and result["replay_same"] is True
    # 2 invoices: the idempotent original + the race content; race resolved safely
    assert result["invoices"] == 2
    assert result["race_codes"] == [200, 201]
    assert result["race_same_invoice"] is True
    # 4 runs: original QUEUED + duplicate REJECTED + 2 race runs (1 queued, 1 rejected)
    assert result["runs"] == 4
    assert result["run_states"] == [
        ("QUEUED", None),
        ("QUEUED", None),
        ("REJECTED", "REJECT"),
        ("REJECTED", "REJECT"),
    ]
    assert result["ledger_events"] == [
        "ingest.accepted",
        "ingest.accepted",
        "ingest.duplicate",
        "ingest.duplicate",
    ]
    assert result["doc_ref"] == f"raw/{body['content_hash']}"
    assert result["presigned"] is True
    assert invoice_id > 0 and entry_id > 0

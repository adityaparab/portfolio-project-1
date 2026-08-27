"""Unit tests for POST /v1/invoices (service faked; no network/DB)."""

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from invoiceops_agent.api.deps import get_ingest_service
from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.schemas.invoices import InvoiceAccepted

TOKEN = {"Authorization": "Bearer dev-service-token"}


@dataclass
class _FakeResult:
    accepted: InvoiceAccepted
    duplicate: bool = False


class _FakeIngestService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ingest(self, upload: object) -> _FakeResult:
        self.calls.append("ingest")
        return _FakeResult(InvoiceAccepted(invoice_id=1, run_id=1, content_hash="a" * 64))


@pytest.fixture
def service() -> _FakeIngestService:
    return _FakeIngestService()


@pytest.fixture
def client(service: _FakeIngestService) -> AsyncClient:
    app: FastAPI = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: service
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _pdf() -> dict[str, tuple[str, bytes, str]]:
    return {"upload": ("inv.pdf", b"%PDF-1.4 fake", "application/pdf")}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_upload_accepted(client: AsyncClient, service: _FakeIngestService) -> None:
    resp = await client.post("/v1/invoices", files=_pdf(), headers=TOKEN)
    assert resp.status_code == 201
    body = resp.json()
    assert body["invoice_id"] == 1
    assert body["run_id"] == 1
    assert body["content_hash"] == "a" * 64
    assert body["status"] == "RECEIVED"
    assert service.calls == ["ingest"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_missing_token_is_401_problem(client: AsyncClient) -> None:
    resp = await client.post("/v1/invoices", files=_pdf())
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_wrong_token_is_401(client: AsyncClient) -> None:
    resp = await client.post("/v1/invoices", files=_pdf(), headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.unit
async def test_idempotency_key_replays_without_reingest(
    client: AsyncClient, service: _FakeIngestService
) -> None:
    headers = {**TOKEN, "Idempotency-Key": "key-1"}
    first = await client.post("/v1/invoices", files=_pdf(), headers=headers)
    second = await client.post("/v1/invoices", files=_pdf(), headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert service.calls == ["ingest"]  # stored response replayed, no re-ingest


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unsupported_content_type_is_422(client: AsyncClient) -> None:
    from invoiceops_agent.api.services.ingest import IngestService
    from invoiceops_agent.api.settings import Settings

    # Real service (no DB touched: validation fails first) for type/size rules.
    class _NullStore:
        async def put(self, *a: object, **k: object) -> None:
            raise AssertionError("must not store")

    app2: FastAPI = create_app(Settings())
    real = IngestService(_NullStore(), None, app2.state.settings)  # type: ignore[arg-type]
    app2.dependency_overrides[get_ingest_service] = lambda: real
    async with AsyncClient(
        transport=ASGITransport(app=app2, raise_app_exceptions=False),
        base_url="http://test",
    ) as c2:
        resp = await c2.post(
            "/v1/invoices",
            files={"upload": ("a.txt", b"hello", "text/plain")},
            headers=TOKEN,
        )
        assert resp.status_code == 422
        assert "Unsupported content type" in resp.text

        resp = await c2.post(
            "/v1/invoices",
            files={"upload": ("empty.pdf", b"", "application/pdf")},
            headers=TOKEN,
        )
        assert resp.status_code == 422
        assert "Empty upload" in resp.text

"""Unit tests for RFC 7807 error handling and idempotency middleware."""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.settings import Settings


@pytest.fixture
def app() -> FastAPI:
    app = create_app(Settings())

    @app.get("/__boom")
    async def boom() -> None:
        raise RuntimeError("sentinel")

    @app.get("/__state")
    async def state(request: Request) -> dict[str, str | None]:
        return {"idempotency_key": getattr(request.state, "idempotency_key", None)}

    return app


@pytest.fixture
def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unknown_route_returns_problem_json(client: AsyncClient) -> None:
    resp = await client.get("/nope")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 404
    assert body["title"]
    assert body["instance"] == "/nope"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unhandled_exception_returns_problem_json_no_leak(client: AsyncClient) -> None:
    resp = await client.get("/__boom")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert "sentinel" not in resp.text


@pytest.mark.asyncio
@pytest.mark.unit
async def test_idempotency_key_attached_to_state(client: AsyncClient) -> None:
    resp = await client.get("/__state", headers={"Idempotency-Key": "abc-123"})
    assert resp.status_code == 200
    assert resp.json() == {"idempotency_key": "abc-123"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_idempotency_key_leaves_state_empty(client: AsyncClient) -> None:
    resp = await client.get("/__state")
    assert resp.status_code == 200
    assert resp.json() == {"idempotency_key": None}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_malformed_idempotency_key_rejected(client: AsyncClient) -> None:
    resp = await client.get("/__state", headers={"Idempotency-Key": "x" * 200})
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")

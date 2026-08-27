"""Unit tests for the email webhook (issue #12) — HMAC vectors, replay, reuse.

Service is faked at the ingest boundary; signature/freshness/replay logic is
what's under test here (full-pipeline reuse is covered by the ingest
integration tests through the same service).
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from invoiceops_agent.api.deps import get_ingest_service
from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.schemas.invoices import InvoiceAccepted
from invoiceops_agent.api.settings import Settings

SECRET = "test-webhook-secret"
TOKEN = {"Authorization": "Bearer test-token"}


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def _body(message_id: str = "m-1", content: bytes = b"%PDF-1.4 x") -> bytes:
    return json.dumps(
        {
            "message_id": message_id,
            "received_at": "2026-08-27T12:00:00Z",
            "attachment": {
                "filename": "inv.pdf",
                "content_type": "application/pdf",
                "content_b64": base64.b64encode(content).decode(),
            },
        }
    ).encode()


@dataclass
class _Result:
    accepted: InvoiceAccepted
    duplicate: bool = False


class _FakeService:
    def __init__(self) -> None:
        self.ingests = 0

    async def ingest(self, upload: Any) -> _Result:
        self.ingests += 1
        return _Result(InvoiceAccepted(invoice_id=7, run_id=7, content_hash="b" * 64))


@pytest.fixture
def service() -> _FakeService:
    return _FakeService()


@pytest.fixture
def app(service: _FakeService) -> FastAPI:
    application = create_app(Settings(service_token="test-token", email_webhook_secret=SECRET))
    application.dependency_overrides[get_ingest_service] = lambda: service
    return application


@pytest.fixture
def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


def _headers(body: bytes, *, timestamp: str | None = None, secret: str = SECRET) -> dict[str, str]:
    ts = timestamp or str(int(time.time()))
    return {
        **TOKEN,
        "X-Signature-Timestamp": ts,
        "X-Signature": _sign(secret, ts, body),
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_valid_signature_ingests(client: AsyncClient, app: FastAPI) -> None:
    body = _body()
    resp = await client.post("/v1/invoices/email-webhook", content=body, headers=_headers(body))
    assert resp.status_code == 201
    assert resp.json()["invoice_id"] == 7


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tampered_body_rejected_401(client: AsyncClient) -> None:
    body = _body()
    headers = _headers(body)
    tampered = _body(message_id="m-2")  # different body, original signature
    resp = await client.post("/v1/invoices/email-webhook", content=tampered, headers=headers)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_wrong_secret_rejected_401(client: AsyncClient) -> None:
    body = _body()
    resp = await client.post(
        "/v1/invoices/email-webhook", content=body, headers=_headers(body, secret="other")
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stale_timestamp_rejected_401(client: AsyncClient) -> None:
    body = _body()
    stale = str(int(time.time()) - 3600)
    resp = await client.post(
        "/v1/invoices/email-webhook", content=body, headers=_headers(body, timestamp=stale)
    )
    assert resp.status_code == 401
    assert "freshness" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_missing_signature_headers_rejected_401(client: AsyncClient) -> None:
    body = _body()
    resp = await client.post(
        "/v1/invoices/email-webhook",
        content=body,
        headers={**TOKEN, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.unit
async def test_replayed_message_id_returns_original_without_reingest(
    client: AsyncClient, service: _FakeService
) -> None:
    body = _body(message_id="replay-1")
    headers = _headers(body)
    first = await client.post("/v1/invoices/email-webhook", content=body, headers=headers)
    second = await client.post("/v1/invoices/email-webhook", content=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200  # replay
    assert first.json() == second.json()
    assert service.ingests == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_invalid_base64_attachment_is_422(client: AsyncClient) -> None:
    payload = {
        "message_id": "m-bad",
        "received_at": "2026-08-27T12:00:00Z",
        "attachment": {
            "filename": "inv.pdf",
            "content_type": "application/pdf",
            "content_b64": "!!!not-base64!!!",
        },
    }
    body = json.dumps(payload).encode()
    resp = await client.post("/v1/invoices/email-webhook", content=body, headers=_headers(body))
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.unit
async def test_webhook_requires_service_token(client: AsyncClient) -> None:
    body = _body()
    resp = await client.post("/v1/invoices/email-webhook", content=body)
    assert resp.status_code == 401  # auth runs before signature checks

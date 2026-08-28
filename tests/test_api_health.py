"""Unit tests for health/readiness endpoints (dependencies mocked)."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from invoiceops_agent.api import health
from invoiceops_agent.api.health import DependencyCheck
from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.settings import Settings


def _ok(name: str = "x") -> DependencyCheck:
    return DependencyCheck(name=name, ok=True)


async def _ok_postgres(dsn: str) -> DependencyCheck:
    return _ok("postgres")


async def _ok_http(name: str, base_url: str, path: str, headers: dict[str, str]) -> DependencyCheck:
    return _ok(name)


async def _bad(name: str) -> DependencyCheck:
    return DependencyCheck(name=name, ok=False, detail="down")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    app = create_app(Settings())
    app.state.graph_runner = object()  # pipeline check: runner present
    monkeypatch.setattr(health, "check_postgres", _ok_postgres)
    monkeypatch.setattr(health, "check_http", _ok_http)
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_healthz_ok(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_readyz_ok(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert [c["name"] for c in body["checks"]] == ["postgres", "minio", "litellm", "pipeline"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_readyz_degraded_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    async def bad_postgres(dsn: str) -> DependencyCheck:
        return await _bad("postgres")

    app = create_app(Settings())
    monkeypatch.setattr(health, "check_postgres", bad_postgres)
    monkeypatch.setattr(health, "check_http", _ok_http)
    client = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )

    resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = resp.json()
    assert body["status"] == "degraded"
    postgres = next(c for c in body["checks"] if c["name"] == "postgres")
    assert postgres["ok"] is False


@pytest.mark.unit
def test_readyz_reports_missing_pipeline_runner() -> None:
    """A dead graph runner must surface in readiness — uploads would queue
    unprocessed otherwise (the background-runner bug)."""

    from invoiceops_agent.api.health import check_pipeline
    from invoiceops_agent.api.main import create_app
    from invoiceops_agent.api.settings import Settings

    app = create_app(Settings())  # lifespan not run: runner unset
    check = check_pipeline(app.state)
    assert check.name == "pipeline"
    assert check.ok is False
    assert "queue unprocessed" in (check.detail or "")

"""SPA serving from the api (single-command deploy): root + client-route
fallback, asset caching, API-route precedence, and the /api/ 404."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoiceops_agent.api.main import create_app
from invoiceops_agent.api.settings import Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def spa_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    (tmp_path / "index.html").write_text("<html><title>InvoiceOps</title></html>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_text("console.log('bundle')")
    monkeypatch.setenv("INVOICEOPS_UI_DIST", str(tmp_path))
    app = create_app(Settings())
    return TestClient(app)


def test_root_serves_index(spa_app: TestClient) -> None:
    response = spa_app.get("/")
    assert response.status_code == 200
    assert "InvoiceOps" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_client_routes_fall_back_to_index(spa_app: TestClient) -> None:
    for path in ("/queue", "/audit", "/queue/7"):
        response = spa_app.get(path)
        assert response.status_code == 200
        assert "InvoiceOps" in response.text


def test_hashed_assets_are_immutable(spa_app: TestClient) -> None:
    response = spa_app.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    assert "bundle" in response.text


def test_api_routes_keep_precedence(spa_app: TestClient) -> None:
    # Registered before the SPA catch-all: still JSON, not index.html
    health = spa_app.get("/healthz")
    assert health.status_code == 200
    assert health.headers["content-type"].startswith("application/json")


def test_dev_proxy_prefix_is_404_not_html(spa_app: TestClient) -> None:
    response = spa_app.get("/api/v1/invoices")
    assert response.status_code == 404


def test_path_traversal_blocked(spa_app: TestClient) -> None:
    response = spa_app.get("/..%2f..%2fetc%2fpasswd")
    assert response.status_code in (404, 400)


def test_no_dist_no_catchall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVOICEOPS_UI_DIST", raising=False)
    app = create_app(Settings())
    with TestClient(app) as client:  # lifespan required for queue routes
        response = client.get("/queue")
        assert response.status_code in (404, 405)  # API-only app: no SPA fallback

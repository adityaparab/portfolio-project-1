"""Unit tests for the extraction agent — offline via cassettes (issue #16)."""

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from invoiceops_agent.agents.extraction import ExtractionAgent, InvoiceExtraction
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient
from invoiceops_agent.gateway_client.errors import GatewayResponseError

DOC_BYTES = b"%PDF-1.4 fake-invoice-bytes"


class _FakeStore:
    """Serves the raw document over a presigned-style local getter."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.fetched: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        self.fetched.append(key)
        return f"memory://{key}"


def _patch_fetch(agent: ExtractionAgent, store: _FakeStore) -> None:
    """Redirect the presigned fetch to the in-memory store."""

    async def fetch(doc_ref: str) -> str:
        url = await store.presigned_get(doc_ref)
        assert url.startswith("memory://")
        return base64.b64encode(DOC_BYTES).decode()

    import httpx

    class _Local(httpx.AsyncClient):
        async def get(self, url: str, **kwargs: Any) -> httpx.Response:  # type: ignore[override]
            return httpx.Response(200, content=DOC_BYTES)

    agent._fetch_b64 = fetch  # type: ignore[method-assign]


def _valid_extraction() -> dict[str, Any]:
    return {
        "vendor_name": "Acme Supplies GmbH",
        "invoice_number": "INV-12345",
        "issue_date": "2026-08-01",
        "due_date": "2026-08-31",
        "currency": "EUR",
        "total_amount": 149.99,
        "tax_total": 24.07,
        "lines": [
            {
                "line_no": "1",
                "description": "Consulting",
                "qty": 3,
                "uom": "DAY",
                "unit_price": 50.0,
                "line_total": 150.0,
            }
        ],
        "confidences": {
            "vendor_name": 0.98,
            "total_amount": 0.95,
            "line[1].qty": 0.9,
        },
    }


def _client(tmp_path: Path) -> GatewayClient:
    return GatewayClient(
        base_url="http://gateway.invalid",
        api_key="sk-test",
        cassette_store=CassetteStore(root=tmp_path / "cassettes"),
        cassette_mode="replay",
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_clean_scan_extraction(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path / "cassettes")
    store.save("extract-vision", "clean-scan", "h", json.dumps(_valid_extraction()))
    agent = ExtractionAgent(store=_FakeStore(DOC_BYTES), gateway=_client(tmp_path))

    class _FetchStore(_FakeStore):
        pass

    _patch_fetch(agent, _FetchStore(DOC_BYTES))

    result = await agent.extract_document("raw/abc", "application/pdf", scenario="clean-scan")
    assert isinstance(result, InvoiceExtraction)
    assert result.vendor_name == "Acme Supplies GmbH"
    assert str(result.total_amount) == "149.99"  # Decimal, not float
    assert result.lines[0].qty == 3
    assert result.min_confidence() == 0.9


@pytest.mark.asyncio
@pytest.mark.unit
async def test_malformed_output_escalates(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path / "cassettes")
    store.save("extract-vision", "noisy-scan", "h", "total gibberish from a bad scan")
    agent = ExtractionAgent(store=_FakeStore(DOC_BYTES), gateway=_client(tmp_path))
    _patch_fetch(agent, _FakeStore(DOC_BYTES))

    with pytest.raises(GatewayResponseError):
        await agent.extract_document("raw/abc", "application/pdf", scenario="noisy-scan")


@pytest.mark.unit
def test_non_iso_date_rejected() -> None:
    payload = _valid_extraction()
    payload["issue_date"] = "01/08/2026"
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        InvoiceExtraction.model_validate(payload)


@pytest.mark.unit
def test_prompt_version_is_pinned_artifact() -> None:
    from invoiceops_agent.agents.prompts import extract_invoice

    assert extract_invoice.PROMPT_VERSION == "extract@v1"
    assert "{filename}" in extract_invoice.USER_TEMPLATE
    assert "confidences" in extract_invoice.SYSTEM

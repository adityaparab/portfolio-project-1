"""Unit tests for near-duplicate detection primitives (issue #23): text
normalization, hashing-embedder determinism + locality, dimension guards."""

import asyncio
import math
from decimal import Decimal

import pytest

from invoiceops_agent.db.models import EMBEDDING_DIM
from invoiceops_agent.tools.near_dup import HashEmbedder, NearDupService, salient_text

pytestmark = pytest.mark.unit


def invoice_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "vendor_name": "Acme Supplies GmbH",
        "invoice_number": "INV-2026-0042",
        "issue_date": "2026-08-14",
        "due_date": "2026-09-13",
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


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(
        sum(x * x for x in a) * sum(y * y for y in b)
    )


def embed(text: str) -> list[float]:
    return asyncio.run(HashEmbedder().embed(text))


def test_salient_text_is_normalized_and_deterministic() -> None:
    text = salient_text(invoice_dict())
    assert text == salient_text(invoice_dict())  # deterministic
    assert "vendor_name:acme supplies gmbh" in text  # casefold + collapse
    assert "total_amount:149.99" in text
    assert "line0.description:widget pro 500" in text

    noisy = invoice_dict(vendor_name="  ACME   Supplies GmbH ")
    assert salient_text(noisy) == text  # spacing/case noise does not matter


def test_salient_text_missing_fields_render_as_unknown() -> None:
    assert "iban:unknown" in salient_text(invoice_dict(iban=None))


def test_hash_embedder_is_deterministic_and_unit_norm() -> None:
    a = embed(salient_text(invoice_dict()))
    assert a == embed(salient_text(invoice_dict()))
    assert len(a) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, rel_tol=1e-9)


def test_locality_small_amount_change_stays_similar() -> None:
    original = salient_text(invoice_dict())
    altered = salient_text(invoice_dict(total_amount=Decimal("150.49")))  # +0.33%
    sim = cosine(embed(original), embed(altered))
    assert sim >= 0.90, f"altered duplicate must stay above threshold, got {sim:.3f}"


def test_distinct_invoices_are_not_similar() -> None:
    other = invoice_dict(
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
    sim = cosine(embed(salient_text(invoice_dict())), embed(salient_text(other)))
    assert sim < 0.5, f"distinct invoices must fall below threshold, got {sim:.3f}"


def test_wrong_dimension_vector_is_rejected_before_any_query() -> None:
    service = NearDupService(HashEmbedder(dim=64))
    with pytest.raises(ValueError, match="dimension"):
        asyncio.run(
            service.find_similar(session=None, invoice_id=1, vector=[0.1] * 8)  # type: ignore[arg-type]
        )

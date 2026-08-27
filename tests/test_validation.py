"""Unit tests for the deterministic validate node (issue #17).

Table-driven cases + hypothesis properties over the math invariants.
Pure functions: no I/O, no clock, no randomness (fastapi/database untouched).
"""

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from invoiceops_agent.agents.extraction import ExtractionLine, InvoiceExtraction
from invoiceops_agent.tools.validation import (
    ReasonCode,
    Severity,
    validate_extraction,
)

VALID_IBAN = "DE89370400440532013000"  # standard mod-97-valid example


def _extraction(**overrides: Any) -> InvoiceExtraction:
    base: dict[str, Any] = {
        "vendor_name": "Acme GmbH",
        "invoice_number": "INV-1",
        "issue_date": "2026-08-01",
        "due_date": "2026-08-31",
        "currency": "EUR",
        "total_amount": Decimal("100.00"),  # net; tax separate
        "tax_total": Decimal("19.00"),
        "iban": VALID_IBAN,
        "lines": [
            ExtractionLine(
                line_no="1",
                description="Consulting",
                qty=Decimal("2"),
                uom="DAY",
                unit_price=Decimal("50.00"),
                tax_code="S",
                line_total=Decimal("100.00"),
            )
        ],
        "confidences": {"total_amount": 0.99},
    }
    base.update(overrides)
    return InvoiceExtraction(**base)


@pytest.mark.unit
def test_clean_extraction_passes_with_no_findings() -> None:
    report = validate_extraction(_extraction())
    assert report.passed
    assert report.checks == ()
    assert report.as_dicts() == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "severity"),
    [
        ("vendor_name", Severity.ERROR),
        ("invoice_number", Severity.ERROR),
        ("issue_date", Severity.ERROR),
        ("currency", Severity.ERROR),
        ("total_amount", Severity.ERROR),
        ("due_date", Severity.WARN),
        ("iban", Severity.WARN),
    ],
)
def test_missing_field_severities(field: str, severity: Severity) -> None:
    extraction = _extraction(**{field: None})
    report = validate_extraction(extraction)
    finding = next(c for c in report.checks if field in c.detail)
    assert finding.code == ReasonCode.SCHEMA_MISSING_FIELD
    assert finding.severity == severity


@pytest.mark.unit
@pytest.mark.parametrize("currency", ["eu", "EURO", "12", "E"])
def test_bad_currency_is_error(currency: str) -> None:
    report = validate_extraction(_extraction(currency=currency))
    assert any(
        c.code == ReasonCode.SCHEMA_BAD_CURRENCY and c.severity == Severity.ERROR
        for c in report.checks
    )


@pytest.mark.unit
def test_valid_iban_passes_mod97() -> None:
    report = validate_extraction(_extraction(iban=VALID_IBAN))
    assert not any(c.code == ReasonCode.SCHEMA_BAD_IBAN for c in report.checks)


@pytest.mark.unit
@pytest.mark.parametrize(
    "iban",
    [
        "DE89370400440532013001",  # bad check digits
        "DE8937040044053201",  # wrong length
        "XX89370400440532013000",  # unknown country → WARN, not checked
    ],
)
def test_invalid_ibans(iban: str) -> None:
    report = validate_extraction(_extraction(iban=iban))
    findings = [c for c in report.checks if c.code == ReasonCode.SCHEMA_BAD_IBAN]
    assert findings
    if iban.startswith("XX"):
        assert findings[0].severity == Severity.WARN
    else:
        assert findings[0].severity == Severity.ERROR


@pytest.mark.unit
def test_off_by_a_cent_fails_line_math_with_delta() -> None:
    extraction = _extraction(total_amount=Decimal("100.01"))
    report = validate_extraction(extraction)
    math_err = next(c for c in report.checks if c.code == ReasonCode.MATH_ERR)
    assert math_err.severity == Severity.ERROR
    assert "delta +0.01" in math_err.detail
    assert not report.passed


@pytest.mark.unit
def test_line_quantity_price_mismatch() -> None:
    lines = [
        ExtractionLine(
            line_no="1",
            qty=Decimal("3"),
            unit_price=Decimal("50.00"),
            tax_code="S",
            line_total=Decimal("100.00"),  # 3 x 50 = 150 != 100
        )
    ]
    report = validate_extraction(_extraction(lines=lines, total_amount=Decimal("100.00")))
    line_err = next(c for c in report.checks if c.code == ReasonCode.MATH_LINE_ERR)
    assert line_err.severity == Severity.ERROR
    assert "delta -50.00" in line_err.detail


@pytest.mark.unit
def test_tax_mismatch_is_error() -> None:
    report = validate_extraction(_extraction(tax_total=Decimal("25.00")))  # S: 19% of 100
    tax_err = next(c for c in report.checks if c.code == ReasonCode.TAX_ERR)
    assert tax_err.severity == Severity.ERROR
    assert "delta +6.00" in tax_err.detail


@pytest.mark.unit
def test_unknown_tax_code_warns_and_skips() -> None:
    lines = [
        ExtractionLine(
            line_no="1",
            qty=Decimal("1"),
            unit_price=Decimal("100.00"),
            tax_code="Q",
            line_total=Decimal("100.00"),
        )
    ]
    report = validate_extraction(_extraction(lines=lines, total_amount=Decimal("100.00")))
    finding = next(c for c in report.checks if c.code == ReasonCode.TAX_UNKNOWN_CODE)
    assert finding.severity == Severity.WARN
    assert report.passed  # WARN never blocks


@pytest.mark.unit
def test_missing_tax_codes_warn() -> None:
    lines = [
        ExtractionLine(
            line_no="1",
            qty=Decimal("1"),
            unit_price=Decimal("100.00"),
            tax_code=None,
            line_total=Decimal("100.00"),
        )
    ]
    report = validate_extraction(_extraction(lines=lines, total_amount=Decimal("100.00")))
    assert any(
        c.code == ReasonCode.TAX_MISSING_CODE and c.severity == Severity.WARN for c in report.checks
    )
    assert report.passed


# ------------------------------------------------------------- hypothesis

_decimals = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("10000"), places=2)


@hyp_settings(max_examples=100, deadline=None)
@given(
    qty=st.integers(min_value=1, max_value=100),
    price=_decimals,
    tax_rate=st.sampled_from(["S", "R", "Z"]),
)
def test_property_consistent_arithmetic_always_passes(
    qty: int, price: Decimal, tax_rate: str
) -> None:
    line_total = Decimal(qty) * price  # int qty keeps the product at 2dp
    total = line_total.quantize(Decimal("0.01"))
    rate = {"S": Decimal("0.19"), "R": Decimal("0.07"), "Z": Decimal("0")}[tax_rate]
    tax = (total * rate).quantize(Decimal("0.01"))
    lines = [
        ExtractionLine(
            line_no="1", qty=Decimal(qty), unit_price=price, tax_code=tax_rate, line_total=total
        )
    ]
    report = validate_extraction(_extraction(lines=lines, total_amount=total, tax_total=tax))
    assert report.passed, report.as_dicts()


@hyp_settings(max_examples=50, deadline=None)
@given(qty=st.integers(min_value=1, max_value=100), price=_decimals)
def test_property_total_drift_always_fails(qty: int, price: Decimal) -> None:
    line_total = Decimal(qty) * price
    drifted = line_total + Decimal("0.01")
    lines = [
        ExtractionLine(
            line_no="1", qty=Decimal(qty), unit_price=price, tax_code=None, line_total=line_total
        )
    ]
    report = validate_extraction(_extraction(lines=lines, total_amount=drifted))
    assert not report.passed
    assert any(c.code == ReasonCode.MATH_ERR for c in report.errors)


@hyp_settings(max_examples=50, deadline=None)
@given(text=st.text(max_size=20))
def test_property_never_crashes_on_arbitrary_ibans(text: str) -> None:
    validate_extraction(_extraction(iban=text))  # any outcome, no exception

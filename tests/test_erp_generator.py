"""Unit tests for the synthetic ERP generator (issue #20): determinism,
documented ground-truth invariants, and the clean-invoice helper contract."""

from datetime import date
from decimal import Decimal

import pytest

from invoiceops_agent.agents.extraction import InvoiceExtraction
from invoiceops_agent.data.erp import (
    DEFAULT_REFERENCE_DATE,
    clean_invoice_for,
    generate,
)
from invoiceops_agent.tools.validation import validate_extraction
from invoiceops_agent.tools.validation_config import TAX_RATES

pytestmark = pytest.mark.unit


def test_same_seed_same_dataset() -> None:
    assert generate(seed=7) == generate(seed=7)


def test_different_seed_differs() -> None:
    a, b = (
        generate(seed=1, vendors=10, purchase_orders=30),
        generate(seed=2, vendors=10, purchase_orders=30),
    )
    assert a != b


def test_invariants_hold() -> None:
    ds = generate()
    vendors = {v.name: v for v in ds.vendors}
    pos = {po.po_number: po for po in ds.purchase_orders}

    assert len(vendors) == len(ds.vendors)  # unique vendor names
    assert len(pos) == len(ds.purchase_orders)  # unique PO numbers
    assert len({gr.gr_number for gr in ds.goods_receipts}) == len(ds.goods_receipts)

    closed = stale = 0
    for po in ds.purchase_orders:
        assert po.currency == vendors[po.vendor_name].currency
        assert po.status in ("OPEN", "CLOSED")
        closed += po.status == "CLOSED"
        stale += (ds.reference_date - po.ordered_at).days > 90
        assert po.lines, "every PO has at least one line"
        for line in po.lines:
            assert line.qty > 0
            assert line.unit_price > 0
            assert line.line_total == line.qty * line.unit_price  # exact math
            assert line.tax_code in TAX_RATES
    assert closed > 0 and stale > 0  # ground truth for policy #24 scenarios

    for gr in ds.goods_receipts:
        po = pos[gr.po_number]
        assert po.ordered_at <= gr.received_at <= ds.reference_date
        received = {str(e["line_no"]): Decimal(str(e["qty"])) for e in gr.received_qty}
        ordered = {line.line_no: line.qty for line in po.lines}
        assert received == ordered  # full receipt — the clean-match invariant


def test_vendor_banking_details_are_valid_iban() -> None:
    for vendor in generate().vendors:
        iban = vendor.bank_details["iban"]
        assert len(iban) == 22 and iban.startswith("DE")
        rearranged = iban[4:] + iban[:4]
        remainder = 0
        for char in rearranged:
            value = int(char) if char.isdigit() else ord(char) - ord("A") + 10
            remainder = (remainder * (10 if char.isdigit() else 100) + value) % 97
        assert remainder == 1  # ISO 13616 mod-97


def test_clean_invoice_helper_yields_valid_passing_extraction() -> None:
    ds = generate()
    po = next(p for p in ds.purchase_orders if p.status == "OPEN")
    vendor = next(v for v in ds.vendors if v.name == po.vendor_name)
    spec = clean_invoice_for(
        po, vendor, invoice_number="INV-TEST-001", issue_date=ds.reference_date
    )

    extraction = InvoiceExtraction.model_validate(spec.extraction_dict())
    report = validate_extraction(extraction)
    assert report.checks == ()  # clean ⇒ zero findings, not just "no ERRORs"


def test_reference_date_bounds_all_generated_dates() -> None:
    ref = date(2025, 1, 31)
    ds = generate(seed=3, vendors=5, purchase_orders=15, reference_date=ref)
    assert ds.reference_date == ref
    assert all(gr.received_at <= ref for gr in ds.goods_receipts)
    assert all(po.ordered_at <= ref for po in ds.purchase_orders)
    # a different anchor changes the dated rows (vendors are date-independent)
    assert ds == generate(seed=3, vendors=5, purchase_orders=15, reference_date=ref)
    assert ds.purchase_orders != generate(seed=3, vendors=5, purchase_orders=15).purchase_orders


def test_default_reference_date_is_pinned() -> None:
    assert date(2026, 9, 1) == DEFAULT_REFERENCE_DATE

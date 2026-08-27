"""Unit + property tests for the deterministic 3-way matcher (issue #21)."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from invoiceops_agent.data.erp import generate
from invoiceops_agent.tools import matching_config as cfg
from invoiceops_agent.tools.matching import (
    GrForMatch,
    InvoiceForMatch,
    InvoiceLineForMatch,
    MatchOutcome,
    MatchResult,
    PoForMatch,
    PoLineForMatch,
    ReasonCode,
    gr_from_erp,
    match3way,
    po_from_erp,
)

pytestmark = pytest.mark.unit


def _dec(value: str) -> Decimal:
    return Decimal(value)


def make_triple(
    *,
    inv_qty: str = "10",
    rec_qty: str = "10",
    po_price: str = "100.00",
    inv_price: str | None = None,
    uom: str = "EA",
    currency: str = "EUR",
    vendor: str = "Acme Corp",
    po_number: str | None = "PO-2026-00001",
) -> tuple[InvoiceForMatch, PoForMatch | None, GrForMatch | None]:
    price = po_price if inv_price is None else inv_price
    po = PoForMatch(
        po_number="PO-2026-00001",
        vendor_name=vendor,
        currency=currency,
        status="OPEN",
        ordered_at=date(2026, 8, 1),
        lines=(PoLineForMatch("1", _dec("10"), uom, _dec(po_price)),),
    )
    gr = GrForMatch(
        gr_number="GR-2026-00001",
        po_number="PO-2026-00001",
        received_qty=(("1", _dec(rec_qty)),),
    )
    invoice = InvoiceForMatch(
        vendor_name=vendor,
        invoice_number="INV-1",
        po_number=po_number,
        currency=currency,
        issue_date=date(2026, 8, 15),
        lines=(InvoiceLineForMatch("1", _dec(inv_qty), uom, _dec(price)),),
    )
    return invoice, (po if po_number else None), (gr if po_number else None)


def codes(result: MatchResult) -> list[str]:
    return [f.code.value for f in result.findings]


# --------------------------------------------------------------- happy & drift


def test_clean_triple_matches_with_zero_delta() -> None:
    result = match3way(*make_triple())
    assert result.outcome is MatchOutcome.MATCH
    assert result.findings == ()
    assert result.normalized_delta == 0.0


def test_price_within_band_is_tolerable_warn() -> None:
    result = match3way(*make_triple(inv_price="101.00"))  # 1.00 <= band max(0.50, 2.00)
    assert result.outcome is MatchOutcome.TOLERABLE
    assert codes(result) == ["PRICE_DRIFT"]
    assert 0.0 < result.normalized_delta < 1.0


def test_price_above_band_is_mismatch_with_exact_delta() -> None:
    result = match3way(*make_triple(inv_price="102.01"))  # band 2.00 exceeded
    assert result.outcome is MatchOutcome.MISMATCH
    finding = result.findings[0]
    assert finding.code is ReasonCode.PRICE_MM
    assert finding.delta == {
        "invoice": "102.01",
        "po": "100.00",
        "delta": "2.01",
        "band": "2",
    }


def test_absolute_floor_keeps_cheap_items_off_the_flag_list() -> None:
    # po price 5.00 -> relative band would be 0.10; the 0.50 floor applies
    result = match3way(*make_triple(po_price="5.00", inv_price="5.40"))
    assert result.outcome is MatchOutcome.TOLERABLE
    result = match3way(*make_triple(po_price="5.00", inv_price="5.51"))
    assert result.outcome is MatchOutcome.MISMATCH
    assert codes(result) == ["PRICE_MM"]


def test_qty_over_receipt_is_mismatch() -> None:
    result = match3way(*make_triple(inv_qty="11"))
    assert result.outcome is MatchOutcome.MISMATCH
    assert result.findings[0].code is ReasonCode.QTY_MM
    assert result.findings[0].delta == {
        "invoice": "11",
        "received": "10",
        "delta": "1",
    }


def test_qty_at_or_below_receipt_is_clean() -> None:
    assert match3way(*make_triple(inv_qty="10")).outcome is MatchOutcome.MATCH
    assert match3way(*make_triple(inv_qty="7", inv_price="100.00")).outcome is MatchOutcome.MATCH


def test_currency_mismatch_is_error() -> None:
    po_kwargs: dict[str, object] = {"currency": "EUR"}
    invoice, po, gr = make_triple(**po_kwargs)  # type: ignore[arg-type]
    invoice = InvoiceForMatch(
        vendor_name=invoice.vendor_name,
        invoice_number=invoice.invoice_number,
        po_number=invoice.po_number,
        currency="USD",
        issue_date=invoice.issue_date,
        lines=invoice.lines,
    )
    result = match3way(invoice, po, gr)
    assert result.outcome is MatchOutcome.MISMATCH
    assert ReasonCode.CCY_MM in [f.code for f in result.findings]


def test_missing_po_reference_and_unknown_po_both_flag() -> None:
    result = match3way(*make_triple(po_number=None))
    assert result.outcome is MatchOutcome.MISMATCH
    assert result.findings[0].code is ReasonCode.MISSING_PO
    assert result.normalized_delta == 1.0

    invoice, _, _ = make_triple(po_number="PO-DOES-NOT-EXIST")
    result = match3way(invoice, None, None)
    assert result.findings[0].code is ReasonCode.MISSING_PO


def test_uom_and_vendor_and_extra_line_findings() -> None:
    invoice, po, gr = make_triple()  # PO says EA on both sides...
    invoice = InvoiceForMatch(
        vendor_name=invoice.vendor_name,
        invoice_number=invoice.invoice_number,
        po_number=invoice.po_number,
        currency=invoice.currency,
        issue_date=invoice.issue_date,
        lines=(InvoiceLineForMatch("1", Decimal("10"), "PCS", Decimal("100.00")),),
    )
    result = match3way(invoice, po, gr)  # ...but the invoice says PCS
    assert ReasonCode.UOM_MM in [f.code for f in result.findings]

    invoice, po, gr = make_triple(vendor="Other GmbH")  # both sides "Other GmbH"...
    invoice = InvoiceForMatch(  # ...but the invoice head says Acme
        vendor_name="Acme Corp",
        invoice_number=invoice.invoice_number,
        po_number=invoice.po_number,
        currency=invoice.currency,
        issue_date=invoice.issue_date,
        lines=invoice.lines,
    )
    result = match3way(invoice, po, gr)
    assert ReasonCode.VENDOR_MM in [f.code for f in result.findings]

    invoice, po, gr = make_triple()
    invoice = InvoiceForMatch(
        vendor_name=invoice.vendor_name,
        invoice_number=invoice.invoice_number,
        po_number=invoice.po_number,
        currency=invoice.currency,
        issue_date=invoice.issue_date,
        lines=(*invoice.lines, InvoiceLineForMatch("9", Decimal("1"), "EA", Decimal("10"))),
    )
    result = match3way(invoice, po, gr)
    assert ReasonCode.LINE_NOT_ON_PO in [f.code for f in result.findings]


def test_missing_receipt_for_invoiced_line_is_error() -> None:
    invoice, po, gr = make_triple()
    assert gr is not None
    empty_gr = GrForMatch(gr.gr_number, gr.po_number, received_qty=())  # nothing received
    result = match3way(invoice, po, empty_gr)
    assert result.outcome is MatchOutcome.MISMATCH
    assert ReasonCode.GR_LINE_MISSING in [f.code for f in result.findings]


def test_vendor_normalization_ignores_case_and_spacing() -> None:
    invoice, po, gr = make_triple(vendor="  ACME   corp ")
    result = match3way(invoice, po, gr)
    assert ReasonCode.VENDOR_MM not in [f.code for f in result.findings]


def test_result_dict_carries_config_version_and_evidence() -> None:
    as_dict = match3way(*make_triple()).as_dict()
    assert as_dict["config_version"] == cfg.VERSION
    assert as_dict["evidence"]["po_number"] == "PO-2026-00001"


# --------------------------------------------------- generated-corpus harness


def test_clean_invoice_from_generated_erp_matches() -> None:
    ds = generate(seed=42, vendors=5, purchase_orders=10)
    po = ds.purchase_orders[0]
    vendor = next(v for v in ds.vendors if v.name == po.vendor_name)
    gr = next(g for g in ds.goods_receipts if g.po_number == po.po_number)

    invoice = InvoiceForMatch(
        vendor_name=vendor.name,
        invoice_number="INV-ERP-1",
        po_number=po.po_number,
        currency=po.currency,
        issue_date=ds.reference_date,
        lines=tuple(
            InvoiceLineForMatch(line.line_no, line.qty, line.uom, line.unit_price)
            for line in po.lines
        ),
    )
    erp_po = po_from_erp(
        po_number=po.po_number,
        vendor_name=po.vendor_name,
        currency=po.currency,
        status=po.status,
        ordered_at=po.ordered_at,
        lines_jsonb=[
            {
                "line_no": line.line_no,
                "description": line.description,
                "qty": str(line.qty),
                "uom": line.uom,
                "unit_price": str(line.unit_price),
                "tax_code": line.tax_code,
            }
            for line in po.lines
        ],
    )
    erp_gr = gr_from_erp(
        gr_number=gr.gr_number,
        po_number=gr.po_number,
        received_jsonb=[
            {"line_no": str(e["line_no"]), "qty": str(e["qty"])} for e in gr.received_qty
        ],
    )
    result = match3way(invoice, erp_po, erp_gr)
    assert result.outcome is MatchOutcome.MATCH
    assert result.findings == ()


# ----------------------------------------------------- boundary property tests

_money = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("10000"), places=2)


@given(po_price=_money)
def test_delta_exactly_at_band_is_within(po_price: Decimal) -> None:
    """Boundary semantics: |Δ| == band is INSIDE (inclusive); +0.01 is out."""
    band = max(cfg.PRICE_ABS_TOLERANCE, cfg.PRICE_PCT_TOLERANCE * po_price)
    at_band = po_price + band
    result = match3way(*make_triple(po_price=str(po_price), inv_price=str(at_band)))
    assert result.outcome is MatchOutcome.TOLERABLE
    assert result.normalized_delta == 1.0

    above = at_band + Decimal("0.01")
    result = match3way(*make_triple(po_price=str(po_price), inv_price=str(above)))
    assert result.outcome is MatchOutcome.MISMATCH
    assert codes(result) == ["PRICE_MM"]


@given(po_price=_money, drift=st.integers(min_value=-400, max_value=400))
def test_finding_iff_delta_exceeds_band(po_price: Decimal, drift: int) -> None:
    """PRICE_MM present ⇔ |Δ| > max(abs, pct·po) — mirrors the spec exactly."""
    delta = Decimal(drift) / Decimal("100")
    invoice_price = po_price + delta
    band = max(cfg.PRICE_ABS_TOLERANCE, cfg.PRICE_PCT_TOLERANCE * po_price)
    result = match3way(*make_triple(po_price=str(po_price), inv_price=str(invoice_price)))
    has_mm = ReasonCode.PRICE_MM in [f.code for f in result.findings]
    assert has_mm == (abs(delta) > band)


@given(qty_received=st.integers(min_value=1, max_value=1000))
def test_qty_finding_iff_over_receipt(qty_received: int) -> None:
    received = Decimal(qty_received)
    for invoiced in (received - 1, received, received + 1):
        result = match3way(*make_triple(inv_qty=str(invoiced), rec_qty=str(received)))
        has_qty_mm = ReasonCode.QTY_MM in [f.code for f in result.findings]
        assert has_qty_mm == (invoiced > received)

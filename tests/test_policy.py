"""Unit tests for the deterministic policy engine (issue #24): rule-table
edges, replayability, version pinning, fail-closed currency handling."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

import invoiceops_agent.tools.policy_config as cfg
from invoiceops_agent.tools.exception_taxonomy import TAXONOMY, ExceptionCode
from invoiceops_agent.tools.near_dup import NearDupHit
from invoiceops_agent.tools.policy import (
    InvoiceFacts,
    PoFacts,
    PolicyContext,
    PolicyReport,
    VendorFacts,
    evaluate,
)

pytestmark = pytest.mark.unit

ISSUE = date(2026, 8, 15)
MASTER_IBAN = "DE02120300000000202051"


def context(
    *,
    amount: str = "1000.00",
    currency: str = "EUR",
    iban: str | None = MASTER_IBAN,
    po: PoFacts | None = None,
    vendor: VendorFacts | None = None,
    near_dup_hits: tuple[NearDupHit, ...] = (),
    issue_date: date | None = ISSUE,
) -> PolicyContext:
    if po is None:
        po = PoFacts(po_number="PO-2026-00001", status="OPEN", ordered_at=date(2026, 8, 1))
    if vendor is None:
        vendor = VendorFacts(name="Acme Supplies GmbH", iban=MASTER_IBAN)
    return PolicyContext(
        invoice=InvoiceFacts(
            invoice_number="INV-1",
            vendor_name=vendor.name,
            po_number=po.po_number if po else None,
            currency=currency,
            total_amount=Decimal(amount),
            iban=iban,
            issue_date=issue_date,
        ),
        po=po,
        vendor=vendor,
        near_dup_hits=near_dup_hits,
    )


def codes(report: PolicyReport) -> list[ExceptionCode]:
    return [f.code for f in report.findings]


# ------------------------------------------------------------------- clean


def test_clean_invoice_passes_with_no_findings() -> None:
    report = evaluate(context())
    assert report.passed
    assert report.findings == ()
    assert report.policy_version == cfg.VERSION


# ------------------------------------------------------------- spend limits


def test_spend_limit_boundaries() -> None:
    assert evaluate(context(amount="2500.00")).passed  # exactly at limit: pass
    over = evaluate(context(amount="2500.01"))
    assert codes(over) == [ExceptionCode.APPROVAL_REQUIRED]
    finding = over.findings[0]
    assert finding.evidence["approver_role"] == "manager"
    assert finding.evidence["over_limit_by"] == "0.01"


def test_approval_matrix_roles_by_band() -> None:
    at_band = evaluate(context(amount="10000.00")).findings[0]
    assert at_band.evidence["approver_role"] == "manager"  # upper bound inclusive
    above = evaluate(context(amount="10000.01")).findings[0]
    assert above.evidence["approver_role"] == "director"


def test_unknown_currency_fails_closed() -> None:
    report = evaluate(context(amount="10.00", currency="CHF"))
    assert codes(report) == [ExceptionCode.APPROVAL_REQUIRED]
    assert "fail closed" in report.findings[0].detail
    assert report.findings[0].evidence["approver_role"] is None


# ---------------------------------------------------------------- stale PO


def test_stale_po_edges() -> None:
    fresh = PoFacts("PO-1", "OPEN", ordered_at=ISSUE - timedelta(days=90))
    assert evaluate(context(po=fresh)).passed  # exactly STALE_PO_DAYS: fresh

    stale = PoFacts("PO-1", "OPEN", ordered_at=ISSUE - timedelta(days=91))
    report = evaluate(context(po=stale))
    assert codes(report) == [ExceptionCode.STALE_PO]
    assert report.findings[0].evidence["age_days"] == 91

    closed = PoFacts("PO-1", "CLOSED", ordered_at=ISSUE - timedelta(days=1))
    report = evaluate(context(po=closed))
    assert codes(report) == [ExceptionCode.STALE_PO]
    assert "CLOSED" in report.findings[0].detail

    both = PoFacts("PO-1", "CLOSED", ordered_at=ISSUE - timedelta(days=400))
    report = evaluate(context(po=both))
    assert len(report.findings) == 1  # one finding, both reasons joined
    assert report.findings[0].detail.count(";") == 1


# ------------------------------------------------------------- bank change


def test_bank_change_detects_difference_with_masked_evidence() -> None:
    changed = context(iban="DE02100500000054540402")
    report = evaluate(changed)
    assert codes(report) == [ExceptionCode.BANK_CHANGE]
    evidence = report.findings[0].evidence
    assert evidence["invoice_iban"] == "DE02**************0402"  # masked, 22 chars
    assert evidence["master_iban"] == "DE02**************2051"


def test_bank_change_ignores_spacing_and_case() -> None:
    spaced = context(iban=" de02 1203 0000 0000 2020 51 ")
    assert evaluate(spaced).passed


def test_missing_iban_or_vendor_is_not_a_policy_failure() -> None:
    assert evaluate(context(iban=None)).passed
    assert evaluate(context(vendor=None)).passed


# ---------------------------------------------------------------- near dup


def test_near_dup_hits_become_dup_near_finding() -> None:
    hits = (NearDupHit(5, 0.97), NearDupHit(9, 0.93))
    report = evaluate(context(near_dup_hits=hits))
    assert codes(report) == [ExceptionCode.DUP_NEAR]
    assert report.findings[0].evidence["hits"] == [
        {"invoice_id": 5, "similarity": 0.97},
        {"invoice_id": 9, "similarity": 0.93},
    ]
    assert "#5" in report.findings[0].detail


# ---------------------------------------------------- replayability & pins


def test_same_context_same_version_is_replayable() -> None:
    ctx = context(amount="5000.00", near_dup_hits=(NearDupHit(3, 0.95),))
    assert evaluate(ctx) == evaluate(ctx)  # identical findings, order included


def test_policy_version_is_pinned_into_findings_and_report() -> None:
    report = evaluate(context(amount="5000.00"))
    assert report.policy_version == cfg.VERSION
    assert all(f.as_dict()["policy_version"] == cfg.VERSION for f in report.findings)


def test_version_bump_produces_distinct_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = context(amount="5000.00")
    before = evaluate(ctx).as_dicts()
    monkeypatch.setattr(cfg, "VERSION", "policy@2-test")
    after = evaluate(ctx).as_dicts()
    assert before[0]["policy_version"] == "policy@v1"
    assert after[0]["policy_version"] == "policy@2-test"


def test_policy_findings_always_carry_taxonomy_severity() -> None:
    # every finding type the engine can emit, in one context each
    reports = [
        evaluate(context(amount="5000.00")),
        evaluate(context(po=PoFacts("PO-1", "CLOSED", ordered_at=ISSUE))),
        evaluate(context(iban="DE02100500000054540402")),
        evaluate(context(near_dup_hits=(NearDupHit(1, 0.9),))),
    ]
    for report in reports:
        for finding in report.findings:
            assert finding.severity is TAXONOMY[finding.code].severity
            assert finding.severity.value == "HIGH"  # v1: policy forces review


def test_findings_dict_shape_feeds_routing() -> None:
    # routing reads severity off the dicts that land in GraphState.policy
    report = evaluate(context(amount="5000.00"))
    as_dicts = report.as_dicts()
    assert as_dicts[0]["severity"] == "HIGH"
    from invoiceops_agent.graph import routing
    from invoiceops_agent.graph.state import GraphState

    state = GraphState(run_id="r", content_hash="h", policy=as_dicts)
    assert routing.route_after_policy(state) == "exception_triage"

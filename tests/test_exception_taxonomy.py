"""Unit tests for the exception taxonomy (issue #22): eval-contract exactness,
mapping totality, precedence, SLA math, and the persistence payload shape."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from invoiceops_agent.tools import matching, validation
from invoiceops_agent.tools.exception_taxonomy import (
    CODE_PRECEDENCE,
    TAXONOMY,
    ExceptionCode,
    ExceptionSeverity,
    TaxonomyMeta,
    code_for_matching,
    code_for_validation,
    draft_from_findings,
    primary_code,
    sla_due_at,
)

pytestmark = pytest.mark.unit

EVALUATION_CODES = {
    "DUP_EXACT",
    "DUP_NEAR",
    "PRICE_MM",
    "QTY_MM",
    "MISSING_PO",
    "BANK_CHANGE",
    "CCY_MM",
    "TAX_ERR",
    "MATH_ERR",
    "STALE_PO",
}


def test_codes_match_evaluation_doc_exactly() -> None:
    assert {c.value for c in ExceptionCode} == EVALUATION_CODES


def test_every_code_has_metadata_and_metadata_is_consistent() -> None:
    for code in ExceptionCode:
        assert code in TAXONOMY
    assert set(TAXONOMY) == set(ExceptionCode)
    for meta in TAXONOMY.values():
        assert isinstance(meta, TaxonomyMeta)
        assert meta.sla_hours > 0
    assert TAXONOMY[ExceptionCode.DUP_EXACT].severity is ExceptionSeverity.CRITICAL
    assert TAXONOMY[ExceptionCode.MATH_ERR].severity is ExceptionSeverity.MEDIUM


def test_precedence_is_a_total_order_over_all_codes() -> None:
    assert sorted(CODE_PRECEDENCE) == sorted(ExceptionCode)
    assert CODE_PRECEDENCE[0] is ExceptionCode.DUP_EXACT
    # severity must be non-increasing along the precedence order
    ranks = {
        ExceptionSeverity.CRITICAL: 3,
        ExceptionSeverity.HIGH: 2,
        ExceptionSeverity.MEDIUM: 1,
        ExceptionSeverity.LOW: 0,
    }
    severities = [ranks[TAXONOMY[c].severity] for c in CODE_PRECEDENCE]
    assert severities == sorted(severities, reverse=True)


def test_primary_code_follows_precedence() -> None:
    codes = {ExceptionCode.MATH_ERR, ExceptionCode.PRICE_MM, ExceptionCode.QTY_MM}
    assert primary_code(codes) is ExceptionCode.PRICE_MM
    assert primary_code([ExceptionCode.MATH_ERR]) is ExceptionCode.MATH_ERR
    with pytest.raises(ValueError):
        primary_code([])


def test_mapping_is_total_for_validation_reason_codes() -> None:
    for reason in validation.ReasonCode:
        check = validation.CheckResult(reason, validation.Severity.ERROR, "detail", field="x")
        assert isinstance(code_for_validation(check), ExceptionCode)


def test_mapping_is_total_for_matching_reason_codes() -> None:
    for reason in matching.ReasonCode:
        finding = matching.MatchFinding(reason, matching.Severity.ERROR, "detail")
        assert isinstance(code_for_matching(finding), ExceptionCode)


def test_missing_field_mapping_is_field_sensitive() -> None:
    cases = {
        "po_number": ExceptionCode.MISSING_PO,
        "currency": ExceptionCode.CCY_MM,
        "total_amount": ExceptionCode.MATH_ERR,
        "tax_total": ExceptionCode.TAX_ERR,
        "iban": ExceptionCode.BANK_CHANGE,
        "vendor_name": ExceptionCode.BANK_CHANGE,
        "invoice_number": ExceptionCode.MATH_ERR,  # documented v1 fallback
        "issue_date": ExceptionCode.MATH_ERR,
    }
    for field, expected in cases.items():
        check = validation.CheckResult(
            validation.ReasonCode.SCHEMA_MISSING_FIELD,
            validation.Severity.ERROR,
            f"missing required field: {field}",
            field=field,
        )
        assert code_for_validation(check) is expected, field


def test_matching_queue_decisions() -> None:
    queue = {
        matching.ReasonCode.VENDOR_MM: ExceptionCode.BANK_CHANGE,
        matching.ReasonCode.UOM_MM: ExceptionCode.QTY_MM,
        matching.ReasonCode.LINE_NOT_ON_PO: ExceptionCode.QTY_MM,
        matching.ReasonCode.GR_LINE_MISSING: ExceptionCode.QTY_MM,
        matching.ReasonCode.PRICE_DRIFT: ExceptionCode.PRICE_MM,
    }
    for reason, expected in queue.items():
        finding = matching.MatchFinding(reason, matching.Severity.ERROR, "detail")
        assert code_for_matching(finding) is expected, reason


def test_sla_due_uses_injected_clock() -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    due = sla_due_at(now, ExceptionCode.DUP_EXACT)
    assert due == datetime(2026, 9, 1, 16, 0, tzinfo=UTC)  # 4h SLA class


def _price_finding(delta: Decimal = Decimal("5")) -> matching.MatchFinding:
    return matching.MatchFinding(
        matching.ReasonCode.PRICE_MM,
        matching.Severity.ERROR,
        "unit price above PO",
        line_no="1",
        delta={"invoice": "105", "po": "100", "delta": str(delta), "band": "2"},
    )


def _math_check() -> validation.CheckResult:
    return validation.CheckResult(
        validation.ReasonCode.MATH_ERR,
        validation.Severity.ERROR,
        "totals mismatch",
        field="total_amount",
    )


def test_draft_from_findings_primary_code_and_evidence() -> None:
    draft = draft_from_findings(
        invoice_id=7,
        run_id=3,
        validation_checks=[_math_check()],
        match_findings=[_price_finding()],
    )
    assert draft.code is ExceptionCode.PRICE_MM  # precedence: PRICE_MM > MATH_ERR
    assert draft.severity is ExceptionSeverity.HIGH
    evidence = draft.evidence_json()
    assert evidence["code"] == "PRICE_MM"
    assert len(evidence["findings"]) == 2
    assert evidence["findings"][0]["code"] == "MATH_ERR"
    assert evidence["findings"][1]["delta"]["delta"] == "5"


def test_draft_ignores_warn_findings_and_requires_an_error() -> None:
    warn = validation.CheckResult(
        validation.ReasonCode.TAX_UNKNOWN_CODE, validation.Severity.WARN, "unknown"
    )
    drift = matching.MatchFinding(
        matching.ReasonCode.PRICE_DRIFT, matching.Severity.WARN, "within band"
    )
    with pytest.raises(ValueError, match="at least one ERROR"):
        draft_from_findings(
            invoice_id=1, run_id=1, validation_checks=[warn], match_findings=[drift]
        )


def test_draft_rejects_severity_metadata_drift() -> None:
    from invoiceops_agent.tools.exception_taxonomy import ExceptionDraft

    with pytest.raises(ValueError, match="severity"):
        ExceptionDraft(
            invoice_id=1,
            run_id=1,
            code=ExceptionCode.MATH_ERR,
            severity=ExceptionSeverity.CRITICAL,  # taxonomy says MEDIUM
            findings=(),
        )

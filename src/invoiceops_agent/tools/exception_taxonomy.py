"""Exception taxonomy: one vocabulary for every failure mode (issue #22).

Exactly the 10 codes from EVALUATION §2 — they double as eval labels, so the
enum is the confusion-matrix contract and must not drift from the doc.

Three layers:

* **Metadata** — severity + default SLA class per code (drives Phase 3 queue
  ordering); ``sla_due_at`` computes a due timestamp from an *injected* clock
  (pure module, no wall reads).
* **Mapping** — every reason code from validation (#17), matching (#21) and
  policy (#24; policy's reason codes are taxonomy codes by design) maps to
  exactly one taxonomy code. Awkward mappings are deliberate, documented AP
  queue decisions:
    - ``VENDOR_MM`` -> ``BANK_CHANGE`` — payee-identity anomaly; same four-eyes
      review queue as bank-detail changes (payment-destination fraud vector)
    - ``UOM_MM`` / ``LINE_NOT_ON_PO`` / ``GR_LINE_MISSING`` -> ``QTY_MM`` —
      quantity semantics broken (different unit, no ordered basis, receipt
      unverifiable)
    - ``SCHEMA_BAD_IBAN`` -> ``BANK_CHANGE``; ``SCHEMA_BAD_CURRENCY`` ->
      ``CCY_MM``; ``SCHEMA_MISSING_FIELD`` maps per-field (see
      ``MISSING_FIELD_CODE``); remaining required fields fall back to
      ``MATH_ERR`` as the generic data-integrity bucket — a known v1
      approximation for rare extraction-quality failures, watched in #45's
      per-anomaly confusion
* **Precedence** — when one run yields several findings, the exception's
  primary code is the most severe (ties broken by business order below);
  deterministic and total.

The ``exceptions`` table persistence shape (#6 schema, Phase 3 API): evidence
JSONB = :meth:`ExceptionDraft.evidence_json` — primary code + severity +
flattened findings with their exact deltas.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from invoiceops_agent.tools import matching, validation

TAXONOMY_VERSION = "taxonomy@1"


class ExceptionCode(StrEnum):
    """Queue vocabulary. ``EVAL_CODES`` is the eval-label contract — exactly
    EVALUATION §2's ten anomaly codes (never rename those); APPROVAL_REQUIRED
    is an internal queue type for spend-limit/approval-matrix breaches (#24):
    a legitimate human-approval need, not an injected anomaly."""

    DUP_EXACT = "DUP_EXACT"
    DUP_NEAR = "DUP_NEAR"
    PRICE_MM = "PRICE_MM"
    QTY_MM = "QTY_MM"
    MISSING_PO = "MISSING_PO"
    BANK_CHANGE = "BANK_CHANGE"
    CCY_MM = "CCY_MM"
    TAX_ERR = "TAX_ERR"
    MATH_ERR = "MATH_ERR"
    STALE_PO = "STALE_PO"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


#: Codes the eval harness treats as anomaly labels (EVALUATION §2 exactly).
EVAL_CODES: frozenset[ExceptionCode] = frozenset(
    {
        ExceptionCode.DUP_EXACT,
        ExceptionCode.DUP_NEAR,
        ExceptionCode.PRICE_MM,
        ExceptionCode.QTY_MM,
        ExceptionCode.MISSING_PO,
        ExceptionCode.BANK_CHANGE,
        ExceptionCode.CCY_MM,
        ExceptionCode.TAX_ERR,
        ExceptionCode.MATH_ERR,
        ExceptionCode.STALE_PO,
    }
)


class ExceptionSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class TaxonomyMeta:
    code: ExceptionCode
    severity: ExceptionSeverity
    description: str
    sla_hours: int  # default SLA class; Phase 3 queue ordering input


TAXONOMY: dict[ExceptionCode, TaxonomyMeta] = {
    ExceptionCode.DUP_EXACT: TaxonomyMeta(
        ExceptionCode.DUP_EXACT,
        ExceptionSeverity.CRITICAL,
        "Exact duplicate of an already-seen invoice (content hash)",
        4,
    ),
    ExceptionCode.DUP_NEAR: TaxonomyMeta(
        ExceptionCode.DUP_NEAR,
        ExceptionSeverity.HIGH,
        "Near-duplicate submission (small edits vs. a prior invoice)",
        4,
    ),
    ExceptionCode.BANK_CHANGE: TaxonomyMeta(
        ExceptionCode.BANK_CHANGE,
        ExceptionSeverity.HIGH,
        "Bank details / payee identity differ from vendor master data",
        8,
    ),
    ExceptionCode.MISSING_PO: TaxonomyMeta(
        ExceptionCode.MISSING_PO,
        ExceptionSeverity.HIGH,
        "No PO reference, or the referenced PO is unknown",
        24,
    ),
    ExceptionCode.PRICE_MM: TaxonomyMeta(
        ExceptionCode.PRICE_MM,
        ExceptionSeverity.HIGH,
        "Unit price above PO beyond tolerance",
        24,
    ),
    ExceptionCode.QTY_MM: TaxonomyMeta(
        ExceptionCode.QTY_MM,
        ExceptionSeverity.HIGH,
        "Invoiced quantity exceeds the goods receipt (or its basis is broken)",
        24,
    ),
    ExceptionCode.CCY_MM: TaxonomyMeta(
        ExceptionCode.CCY_MM,
        ExceptionSeverity.MEDIUM,
        "Currency differs from PO (or is unusable)",
        48,
    ),
    ExceptionCode.STALE_PO: TaxonomyMeta(
        ExceptionCode.STALE_PO,
        ExceptionSeverity.HIGH,  # paying against an expired/closed PO must force review
        "PO closed or ordered too long before the invoice",
        24,
    ),
    ExceptionCode.APPROVAL_REQUIRED: TaxonomyMeta(
        ExceptionCode.APPROVAL_REQUIRED,
        ExceptionSeverity.HIGH,
        "Invoice requires human approval (spend limit / approval matrix)",
        24,
    ),
    ExceptionCode.TAX_ERR: TaxonomyMeta(
        ExceptionCode.TAX_ERR,
        ExceptionSeverity.MEDIUM,
        "Tax computation inconsistent",
        48,
    ),
    ExceptionCode.MATH_ERR: TaxonomyMeta(
        ExceptionCode.MATH_ERR,
        ExceptionSeverity.MEDIUM,
        "Line sums do not reconcile with the stated total",
        48,
    ),
}

# Primary-code precedence when a run carries multiple findings: severity
# first, then business-criticality within a severity class.
CODE_PRECEDENCE: tuple[ExceptionCode, ...] = (
    ExceptionCode.DUP_EXACT,
    ExceptionCode.DUP_NEAR,
    ExceptionCode.BANK_CHANGE,
    ExceptionCode.MISSING_PO,
    ExceptionCode.PRICE_MM,
    ExceptionCode.QTY_MM,
    ExceptionCode.APPROVAL_REQUIRED,
    ExceptionCode.STALE_PO,
    ExceptionCode.CCY_MM,
    ExceptionCode.TAX_ERR,
    ExceptionCode.MATH_ERR,
)

# SCHEMA_MISSING_FIELD -> the AP queue the missing field actually breaks.
MISSING_FIELD_CODE: dict[str, ExceptionCode] = {
    "po_number": ExceptionCode.MISSING_PO,
    "currency": ExceptionCode.CCY_MM,
    "total_amount": ExceptionCode.MATH_ERR,
    "tax_total": ExceptionCode.TAX_ERR,
    "iban": ExceptionCode.BANK_CHANGE,
    "vendor_name": ExceptionCode.BANK_CHANGE,  # payee identity unverifiable
}
_MISSING_FIELD_FALLBACK = ExceptionCode.MATH_ERR  # documented v1 approximation

_VALIDATION_CODE: dict[validation.ReasonCode, ExceptionCode] = {
    validation.ReasonCode.SCHEMA_MISSING_FIELD: ExceptionCode.MATH_ERR,  # per-field override below
    validation.ReasonCode.SCHEMA_BAD_CURRENCY: ExceptionCode.CCY_MM,
    validation.ReasonCode.SCHEMA_BAD_IBAN: ExceptionCode.BANK_CHANGE,
    validation.ReasonCode.MATH_ERR: ExceptionCode.MATH_ERR,
    validation.ReasonCode.MATH_LINE_ERR: ExceptionCode.MATH_ERR,
    validation.ReasonCode.TAX_ERR: ExceptionCode.TAX_ERR,
    validation.ReasonCode.TAX_UNKNOWN_CODE: ExceptionCode.TAX_ERR,
    validation.ReasonCode.TAX_MISSING_CODE: ExceptionCode.TAX_ERR,
}

_MATCHING_CODE: dict[matching.ReasonCode, ExceptionCode] = {
    matching.ReasonCode.MISSING_PO: ExceptionCode.MISSING_PO,
    matching.ReasonCode.PRICE_MM: ExceptionCode.PRICE_MM,
    matching.ReasonCode.PRICE_DRIFT: ExceptionCode.PRICE_MM,  # WARN — never opens an exception
    matching.ReasonCode.QTY_MM: ExceptionCode.QTY_MM,
    matching.ReasonCode.CCY_MM: ExceptionCode.CCY_MM,
    matching.ReasonCode.VENDOR_MM: ExceptionCode.BANK_CHANGE,
    matching.ReasonCode.UOM_MM: ExceptionCode.QTY_MM,
    matching.ReasonCode.LINE_NOT_ON_PO: ExceptionCode.QTY_MM,
    matching.ReasonCode.GR_LINE_MISSING: ExceptionCode.QTY_MM,
}


def code_for_validation(check: validation.CheckResult) -> ExceptionCode:
    """Validation finding -> taxonomy code (field-sensitive for missing fields)."""
    if check.code is validation.ReasonCode.SCHEMA_MISSING_FIELD and check.field:
        return MISSING_FIELD_CODE.get(check.field, _MISSING_FIELD_FALLBACK)
    return _VALIDATION_CODE[check.code]


def code_for_matching(finding: matching.MatchFinding) -> ExceptionCode:
    return _MATCHING_CODE[finding.code]


def primary_code(codes: Iterable[ExceptionCode]) -> ExceptionCode:
    """Most-precedent code among those present (deterministic total order)."""
    present = set(codes)
    for code in CODE_PRECEDENCE:
        if code in present:
            return code
    raise ValueError("primary_code() called with no codes")


def sla_due_at(now: datetime, code: ExceptionCode) -> datetime:
    """SLA due timestamp from an injected clock (callers own wall time)."""
    return now + timedelta(hours=TAXONOMY[code].sla_hours)


@dataclass(frozen=True)
class ExceptionDraft:
    """Persistence payload for the ``exceptions`` table (written in #25)."""

    invoice_id: int
    run_id: int | None
    code: ExceptionCode
    severity: ExceptionSeverity
    findings: tuple[dict[str, Any], ...]  # flattened finding dicts w/ deltas

    @property
    def meta(self) -> TaxonomyMeta:
        return TAXONOMY[self.code]

    def evidence_json(self) -> dict[str, Any]:
        """Evidence JSONB contract: primary code + severity + all findings."""
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "findings": list(self.findings),
            "taxonomy_version": TAXONOMY_VERSION,
        }

    def __post_init__(self) -> None:
        if self.severity is not TAXONOMY[self.code].severity:
            raise ValueError(
                f"severity {self.severity.value} does not match taxonomy for "
                f"{self.code.value} ({TAXONOMY[self.code].severity.value})"
            )


def draft_from_findings(
    *,
    invoice_id: int,
    run_id: int | None,
    validation_checks: Sequence[validation.CheckResult] = (),
    match_findings: Sequence[matching.MatchFinding] = (),
) -> ExceptionDraft:
    """Build the exception record for a run from its typed findings.

    Only ERROR-severity inputs open exceptions (WARNs continue the pipeline);
    the primary code follows :data:`CODE_PRECEDENCE`.
    """
    entries: list[tuple[ExceptionCode, dict[str, Any]]] = []
    for check in validation_checks:
        if check.severity != validation.Severity.ERROR:
            continue
        entries.append((code_for_validation(check), check.as_dict()))
    for finding in match_findings:
        if finding.severity != matching.Severity.ERROR:
            continue
        entries.append((code_for_matching(finding), finding.as_dict()))

    if not entries:
        raise ValueError("draft_from_findings requires at least one ERROR finding")
    code = primary_code(c for c, _ in entries)
    return ExceptionDraft(
        invoice_id=invoice_id,
        run_id=run_id,
        code=code,
        severity=TAXONOMY[code].severity,
        findings=tuple(payload for _, payload in entries),
    )

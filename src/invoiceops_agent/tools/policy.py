"""Deterministic policy engine (issue #24, ADR 0001).

Rules-as-data evaluated against the full run context — no LLM, no I/O, no
wall clock (the caller injects the reference date). Same context + same
policy version ⇒ identical findings: replayable by construction, so an
auditor can recompute every verdict.

Rules (v1):

* ``spend-limit`` — amount at/below the currency's auto-approve limit passes;
  above it (or a currency without a band — fail closed) → APPROVAL_REQUIRED
  (HIGH), evidence carries the approver role from the approval matrix.
* ``stale-po`` — PO CLOSED, or ordered more than STALE_PO_DAYS before the
  invoice issue date → STALE_PO (HIGH). Exactly STALE_PO_DAYS is fresh.
* ``bank-change`` — invoice IBAN differs from vendor master (normalized) →
  BANK_CHANGE (HIGH).
* ``near-dup`` — pgvector near-duplicate hits present → DUP_NEAR (HIGH)
  (hits computed upstream by the near-dup service, #23; policy only judges).

Vendor risk flags ride along as context for triage, not as findings — no
eval label exists for them in v1. DUP_EXACT stays at ingest (#13): a byte-
identical resubmission never reaches this engine.

Severities come from the taxonomy (exception_taxonomy.TAXONOMY) so queue
metadata and routing read one table; every policy finding's severity is
HIGH in v1 — policy exists to force human review, never to auto-reject.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from invoiceops_agent.tools import policy_config as cfg
from invoiceops_agent.tools.exception_taxonomy import (
    TAXONOMY,
    ExceptionCode,
    ExceptionSeverity,
)
from invoiceops_agent.tools.near_dup import NearDupHit


@dataclass(frozen=True)
class InvoiceFacts:
    invoice_number: str | None
    vendor_name: str | None
    po_number: str | None
    currency: str | None
    total_amount: Decimal | None
    iban: str | None
    issue_date: date | None


@dataclass(frozen=True)
class PoFacts:
    po_number: str
    status: str  # OPEN | CLOSED (ERP sim vocabulary)
    ordered_at: date | None
    currency: str | None = None


@dataclass(frozen=True)
class VendorFacts:
    name: str
    iban: str | None  # vendor master banking details
    is_active: bool = True
    risk_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyContext:
    invoice: InvoiceFacts
    po: PoFacts | None = None  # None already surfaced as MISSING_PO by the matcher
    vendor: VendorFacts | None = None
    near_dup_hits: tuple[NearDupHit, ...] = ()
    reference_date: date | None = None  # injected clock; issue_date anchors staleness


@dataclass(frozen=True)
class PolicyFinding:
    rule_id: str
    code: ExceptionCode
    severity: ExceptionSeverity  # always TAXONOMY[code].severity — enforced below
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = TAXONOMY[self.code].severity
        if self.severity is not expected:
            raise ValueError(
                f"policy finding severity {self.severity.value} != taxonomy "
                f"{self.code.value} severity {expected.value}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "code": self.code.value,
            "severity": self.severity.value,
            "detail": self.detail,
            "evidence": self.evidence,
            "policy_version": cfg.VERSION,
        }


@dataclass(frozen=True)
class PolicyReport:
    findings: tuple[PolicyFinding, ...] = ()
    policy_version: str = cfg.VERSION

    @property
    def passed(self) -> bool:
        """True when no finding — the invoice may proceed to the gate."""
        return not self.findings

    def as_dicts(self) -> list[dict[str, Any]]:
        return [f.as_dict() for f in self.findings]


def evaluate(context: PolicyContext) -> PolicyReport:
    """Pure rule evaluation; identical output for identical inputs+version."""
    findings: list[PolicyFinding | None] = []
    findings.append(_rule_spend_limit(context))
    findings.append(_rule_stale_po(context))
    findings.append(_rule_bank_change(context))
    findings.extend(_rule_near_dup(context))
    present = tuple(f for f in findings if f is not None)
    return PolicyReport(findings=present, policy_version=cfg.VERSION)


# --------------------------------------------------------------------- rules


def _rule_spend_limit(context: PolicyContext) -> PolicyFinding | None:
    inv = context.invoice
    if inv.total_amount is None:
        return None  # validation owns missing-field failures
    currency = (inv.currency or "").upper()
    limit = cfg.AUTO_APPROVE_LIMITS.get(currency)
    if limit is not None and inv.total_amount <= limit:
        return None
    if limit is None:
        detail = (
            f"no spend band defined for {currency or 'missing'} currency — "
            "fail closed (no auto-approval without a configured limit)"
        )
        amount_note = "no-band"
    else:
        detail = f"amount {inv.total_amount} exceeds auto-approve limit {limit} ({currency})"
        amount_note = str(inv.total_amount - limit)
    return PolicyFinding(
        rule_id="spend-limit",
        code=ExceptionCode.APPROVAL_REQUIRED,
        severity=TAXONOMY[ExceptionCode.APPROVAL_REQUIRED].severity,
        detail=detail,
        evidence={
            "amount": str(inv.total_amount),
            "currency": currency,
            "limit": str(limit) if limit is not None else None,
            "over_limit_by": amount_note,
            "approver_role": _approver_role(currency, inv.total_amount),
        },
    )


def _approver_role(currency: str, amount: Decimal) -> str | None:
    for upper, role in cfg.APPROVAL_MATRIX.get(currency, ()):
        if amount <= upper:
            return role
    return None


def _rule_stale_po(context: PolicyContext) -> PolicyFinding | None:
    po = context.po
    if po is None or context.invoice.issue_date is None:
        return None
    reasons: list[str] = []
    age_days: int | None = None
    if po.status.upper() == "CLOSED":
        reasons.append(f"PO status is {po.status}")
    if po.ordered_at is not None:
        age_days = (context.invoice.issue_date - po.ordered_at).days
        if age_days > cfg.STALE_PO_DAYS:
            reasons.append(f"ordered {age_days} days before invoice (>{cfg.STALE_PO_DAYS})")
    if not reasons:
        return None
    return PolicyFinding(
        rule_id="stale-po",
        code=ExceptionCode.STALE_PO,
        severity=TAXONOMY[ExceptionCode.STALE_PO].severity,
        detail="; ".join(reasons),
        evidence={"po_number": po.po_number, "status": po.status, "age_days": age_days},
    )


def _rule_bank_change(context: PolicyContext) -> PolicyFinding | None:
    inv_iban = context.invoice.iban
    vendor = context.vendor
    if inv_iban is None or vendor is None or vendor.iban is None:
        return None
    if _norm_iban(inv_iban) == _norm_iban(vendor.iban):
        return None
    return PolicyFinding(
        rule_id="bank-change",
        code=ExceptionCode.BANK_CHANGE,
        severity=TAXONOMY[ExceptionCode.BANK_CHANGE].severity,
        detail="invoice IBAN differs from vendor master data",
        evidence={
            "vendor": vendor.name,
            "invoice_iban": _mask_iban(inv_iban),
            "master_iban": _mask_iban(vendor.iban),
        },
    )


def _rule_near_dup(context: PolicyContext) -> list[PolicyFinding]:
    if not context.near_dup_hits:
        return []
    best = max(context.near_dup_hits, key=lambda h: h.similarity)
    return [
        PolicyFinding(
            rule_id="near-dup",
            code=ExceptionCode.DUP_NEAR,
            severity=TAXONOMY[ExceptionCode.DUP_NEAR].severity,
            detail=(
                f"{len(context.near_dup_hits)} near-duplicate invoice(s) found; "
                f"best similarity {best.similarity:.4f} vs #{best.invoice_id}"
            ),
            evidence={
                "hits": [
                    {"invoice_id": h.invoice_id, "similarity": round(h.similarity, 6)}
                    for h in context.near_dup_hits
                ]
            },
        )
    ]


def _norm_iban(iban: str) -> str:
    return iban.replace(" ", "").upper()


def _mask_iban(iban: str) -> str:
    """Evidence hygiene: keep country+check digits + last 4, mask the rest."""
    compact = _norm_iban(iban)
    if len(compact) <= 8:
        return compact
    return f"{compact[:4]}{'*' * (len(compact) - 8)}{compact[-4:]}"

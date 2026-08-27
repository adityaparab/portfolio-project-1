"""Deterministic 3-way matcher: invoice <-> PO <-> goods receipt (issue #21).

Pure functions only — no I/O, no clock, no randomness (ADR 0001). Tolerance
bands come from :mod:`matching_config` (versioned). Every violation produces
a typed finding carrying exact Decimal deltas, which double as the evidence
package for triage (#30) and eval confusion matrices (#45).

Compared:
* header — PO reference present (MISSING_PO), vendor identity, currency
* per invoice line — PO line exists (LINE_NOT_ON_PO), receipt exists for the
  line (GR_LINE_MISSING), invoice qty <= received qty + tolerance (QTY_MM),
  unit price within band (PRICE_MM / PRICE_DRIFT when inside the band), UoM

Outcome aggregation: any ERROR finding -> MISMATCH; else any WARN finding
(within-band drift) -> TOLERABLE; else MATCH. MISMATCH routes to triage and
never reaches the confidence gate (#26); TOLERABLE reaches it with a nonzero
``normalized_delta`` so within-band drift still costs confidence.

``normalized_delta`` (gate term w2): 1.0 - match_score where match_score is
``max(0, 1 - worst |delta|/band_limit ratio)`` over the continuous checks
(price, qty). Discrete mismatches (currency/UoM/vendor/missing PO) score 0.0
exactly, so a clean-but-drifting invoice differentiates from a broken one.

Finding vocabulary vs. the eval taxonomy (#22): PRICE_MM, QTY_MM, CCY_MM and
MISSING_PO are eval labels directly; VENDOR_MM, UOM_MM, LINE_NOT_ON_PO and
GR_LINE_MISSING are matcher-level codes whose taxonomy mapping and precedence
are formalized in #22.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from invoiceops_agent.tools import matching_config as cfg


class MatchOutcome(StrEnum):
    MATCH = "MATCH"
    TOLERABLE = "TOLERABLE"
    MISMATCH = "MISMATCH"


class Severity(StrEnum):
    ERROR = "ERROR"  # routes to exception triage
    WARN = "WARN"  # recorded; continues (within-band drift)


class ReasonCode(StrEnum):
    MISSING_PO = "MISSING_PO"
    VENDOR_MM = "VENDOR_MM"
    CCY_MM = "CCY_MM"
    UOM_MM = "UOM_MM"
    LINE_NOT_ON_PO = "LINE_NOT_ON_PO"
    GR_LINE_MISSING = "GR_LINE_MISSING"
    QTY_MM = "QTY_MM"
    PRICE_MM = "PRICE_MM"
    PRICE_DRIFT = "PRICE_DRIFT"  # within tolerance band — WARN only


@dataclass(frozen=True)
class InvoiceLineForMatch:
    line_no: str
    qty: Decimal
    uom: str | None
    unit_price: Decimal


@dataclass(frozen=True)
class InvoiceForMatch:
    vendor_name: str | None
    invoice_number: str | None
    po_number: str | None
    currency: str | None
    issue_date: date | None
    lines: tuple[InvoiceLineForMatch, ...]


@dataclass(frozen=True)
class PoLineForMatch:
    line_no: str
    qty: Decimal
    uom: str | None
    unit_price: Decimal


@dataclass(frozen=True)
class PoForMatch:
    po_number: str
    vendor_name: str | None
    currency: str | None
    status: str  # informational: stale/closed-PO verdicts belong to policy (#24)
    ordered_at: date | None
    lines: tuple[PoLineForMatch, ...]


@dataclass(frozen=True)
class GrForMatch:
    gr_number: str
    po_number: str
    received_qty: tuple[tuple[str, Decimal], ...]  # (po line_no, qty) pairs


@dataclass(frozen=True)
class MatchFinding:
    code: ReasonCode
    severity: Severity
    detail: str
    line_no: str | None = None
    delta: dict[str, str] | None = None  # exact decimal deltas as strings

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "detail": self.detail,
            "line_no": self.line_no,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class MatchResult:
    outcome: MatchOutcome
    findings: tuple[MatchFinding, ...] = field(default_factory=tuple)
    normalized_delta: float = 1.0  # [0,1]; 0 = perfect continuous match
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "findings": [f.as_dict() for f in self.findings],
            "normalized_delta": self.normalized_delta,
            "evidence": self.evidence,
            "config_version": cfg.VERSION,
        }


def match3way(
    invoice: InvoiceForMatch, po: PoForMatch | None, gr: GrForMatch | None
) -> MatchResult:
    """Compare the triple deterministically. ``po``/``gr`` are the ERP lookup
    results (``None`` = not found); the invoice side is the extraction."""
    findings: list[MatchFinding | None] = []

    if po is None or invoice.po_number is None:
        return MatchResult(
            outcome=MatchOutcome.MISMATCH,
            findings=(
                MatchFinding(
                    ReasonCode.MISSING_PO,
                    Severity.ERROR,
                    f"PO reference {invoice.po_number!r} not found in ERP"
                    if invoice.po_number
                    else "invoice carries no PO reference",
                    delta={"po_number": str(invoice.po_number)},
                ),
            ),
            normalized_delta=1.0,
            evidence={"po_number": invoice.po_number, "config_version": cfg.VERSION},
        )

    findings.append(_check_vendor(invoice, po))
    findings.append(_check_currency(invoice, po))

    received = dict(gr.received_qty) if gr is not None else {}
    po_lines = {line.line_no: line for line in po.lines}
    ratios: list[Decimal] = []

    for inv_line in invoice.lines:
        po_line = po_lines.get(inv_line.line_no)
        if po_line is None:
            findings.append(
                MatchFinding(
                    ReasonCode.LINE_NOT_ON_PO,
                    Severity.ERROR,
                    f"line {inv_line.line_no} is not on PO {po.po_number}",
                    line_no=inv_line.line_no,
                )
            )
            continue

        if inv_line.line_no not in received:
            findings.append(
                MatchFinding(
                    ReasonCode.GR_LINE_MISSING,
                    Severity.ERROR,
                    f"no goods receipt recorded for PO line {inv_line.line_no} — "
                    "receipt cannot be verified",
                    line_no=inv_line.line_no,
                )
            )
        else:
            findings.append(_check_qty(inv_line, received[inv_line.line_no], ratios))

        findings.append(_check_price(inv_line, po_line, ratios))
        findings.append(_check_uom(inv_line, po_line))

    present = [f for f in findings if f is not None]
    errors = [f for f in present if f.severity == Severity.ERROR]
    warnings = [f for f in present if f.severity == Severity.WARN]

    if errors:
        outcome = MatchOutcome.MISMATCH
    elif warnings:
        outcome = MatchOutcome.TOLERABLE
    else:
        outcome = MatchOutcome.MATCH

    worst_ratio = max(ratios, default=Decimal("0"))
    return MatchResult(
        outcome=outcome,
        findings=tuple(present),
        normalized_delta=float(min(Decimal("1"), worst_ratio)),
        evidence={
            "po_number": po.po_number,
            "gr_number": gr.gr_number if gr is not None else None,
            "po_status": po.status,
            "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "ordered_at": po.ordered_at.isoformat() if po.ordered_at else None,
            "invoice_number": invoice.invoice_number,
            "config_version": cfg.VERSION,
        },
    )


def po_from_erp(
    *,
    po_number: str,
    vendor_name: str | None,
    currency: str | None,
    status: str,
    ordered_at: date | None,
    lines_jsonb: list[dict[str, Any]],
) -> PoForMatch:
    """Parse the ERP JSONB shape (seed_erp contract: numerics as decimal
    strings) into the matcher's typed input."""
    return PoForMatch(
        po_number=po_number,
        vendor_name=vendor_name,
        currency=currency,
        status=status,
        ordered_at=ordered_at,
        lines=tuple(
            PoLineForMatch(
                line_no=str(line["line_no"]),
                qty=Decimal(str(line["qty"])),
                uom=line.get("uom"),
                unit_price=Decimal(str(line["unit_price"])),
            )
            for line in lines_jsonb
        ),
    )


def gr_from_erp(
    *, gr_number: str, po_number: str, received_jsonb: list[dict[str, Any]]
) -> GrForMatch:
    return GrForMatch(
        gr_number=gr_number,
        po_number=po_number,
        received_qty=tuple(
            (str(entry["line_no"]), Decimal(str(entry["qty"]))) for entry in received_jsonb
        ),
    )


# --------------------------------------------------------------------- internals


def _check_vendor(invoice: InvoiceForMatch, po: PoForMatch) -> MatchFinding | None:
    if invoice.vendor_name is None or po.vendor_name is None:
        return None
    if _norm_name(invoice.vendor_name) == _norm_name(po.vendor_name):
        return None
    return MatchFinding(
        ReasonCode.VENDOR_MM,
        Severity.ERROR,
        f"invoice vendor {invoice.vendor_name!r} != PO vendor {po.vendor_name!r}",
        delta={"invoice": invoice.vendor_name, "po": po.vendor_name},
    )


def _check_currency(invoice: InvoiceForMatch, po: PoForMatch) -> MatchFinding | None:
    if invoice.currency is None or po.currency is None:
        return None
    if invoice.currency.upper() == po.currency.upper():
        return None
    return MatchFinding(
        ReasonCode.CCY_MM,
        Severity.ERROR,
        f"invoice currency {invoice.currency} != PO currency {po.currency}",
        delta={"invoice": invoice.currency.upper(), "po": po.currency.upper()},
    )


def _check_uom(inv_line: InvoiceLineForMatch, po_line: PoLineForMatch) -> MatchFinding | None:
    if inv_line.uom is None or po_line.uom is None:
        return None
    if inv_line.uom.upper() == po_line.uom.upper():
        return None
    return MatchFinding(
        ReasonCode.UOM_MM,
        Severity.ERROR,
        f"line {inv_line.line_no}: invoice UoM {inv_line.uom!r} != PO UoM {po_line.uom!r}",
        line_no=inv_line.line_no,
        delta={"invoice": inv_line.uom.upper(), "po": po_line.uom.upper()},
    )


def _check_qty(
    inv_line: InvoiceLineForMatch, received_qty: Decimal, ratios: list[Decimal]
) -> MatchFinding | None:
    delta = inv_line.qty - received_qty
    if delta <= cfg.QTY_TOLERANCE:
        return None  # invoiced at/below receipt (under-billing is not an AP exception)
    ratios.append(_ratio(delta, Decimal("1")))
    return MatchFinding(
        ReasonCode.QTY_MM,
        Severity.ERROR,
        f"line {inv_line.line_no}: invoiced qty {inv_line.qty} exceeds received "
        f"{received_qty} by {delta}",
        line_no=inv_line.line_no,
        delta={"invoice": str(inv_line.qty), "received": str(received_qty), "delta": str(delta)},
    )


def _check_price(
    inv_line: InvoiceLineForMatch, po_line: PoLineForMatch, ratios: list[Decimal]
) -> MatchFinding | None:
    delta = inv_line.unit_price - po_line.unit_price
    band = _price_band(po_line.unit_price)
    ratio = _ratio(abs(delta), band)
    ratios.append(ratio)
    if abs(delta) <= band:
        if delta == 0:
            return None
        return MatchFinding(
            ReasonCode.PRICE_DRIFT,
            Severity.WARN,
            f"line {inv_line.line_no}: unit price {inv_line.unit_price} drifts "
            f"{delta:+} from PO {po_line.unit_price} (within band {band})",
            line_no=inv_line.line_no,
            delta={
                "invoice": str(inv_line.unit_price),
                "po": str(po_line.unit_price),
                "delta": str(delta),
                "band": str(band),
            },
        )
    return MatchFinding(
        ReasonCode.PRICE_MM,
        Severity.ERROR,
        f"line {inv_line.line_no}: unit price {inv_line.unit_price} exceeds PO "
        f"{po_line.unit_price} by {delta:+} (band {band})",
        line_no=inv_line.line_no,
        delta={
            "invoice": str(inv_line.unit_price),
            "po": str(po_line.unit_price),
            "delta": str(delta),
            "band": str(band),
        },
    )


def _price_band(po_unit_price: Decimal) -> Decimal:
    """Effective tolerance: relative band with an absolute floor (config).
    ``normalize()`` only trims trailing zeros — the value (and thus boundary
    semantics) is untouched."""
    return max(cfg.PRICE_ABS_TOLERANCE, cfg.PRICE_PCT_TOLERANCE * po_unit_price).normalize()


def _ratio(delta: Decimal, band: Decimal) -> Decimal:
    """|delta|/band as [0,1]-clipped Decimal; band=0 means any delta is full."""
    if band <= 0:
        return Decimal("1") if delta > 0 else Decimal("0")
    clipped = delta / band
    return min(Decimal("1"), clipped) if clipped > 0 else Decimal("0")


def _norm_name(name: str) -> str:
    return " ".join(name.casefold().split())

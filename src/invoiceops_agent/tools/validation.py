"""Deterministic validation of extraction output (issue #17 — pure, no I/O).

Checks (each emits a typed ``CheckResult`` with a reason code consumable by
the exception taxonomy in #22):

* required-field presence (ERROR/WARN split from versioned config)
* currency format (ISO-4217 shape)
* IBAN structure (length table + ISO 13616 mod-97)
* line math: Σ line_total == total_amount (exact Decimal; off-by-a-cent fails
  with the precise delta reported) and qty x unit_price == line_total per line
* tax recomputation from the line tax codes (jurisdiction stub; unknown or
  missing codes WARN and skip — never a hard failure on stub data)
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from invoiceops_agent.agents.extraction import InvoiceExtraction
from invoiceops_agent.tools import validation_config as cfg


class Severity(StrEnum):
    ERROR = "ERROR"  # hard failure → routes to triage (route_after_validate)
    WARN = "WARN"  # soft finding → continues, recorded


class ReasonCode(StrEnum):
    SCHEMA_MISSING_FIELD = "SCHEMA_MISSING_FIELD"
    SCHEMA_BAD_CURRENCY = "SCHEMA_BAD_CURRENCY"
    SCHEMA_BAD_IBAN = "SCHEMA_BAD_IBAN"
    MATH_ERR = "MATH_ERR"  # taxonomy: totals mismatch
    MATH_LINE_ERR = "MATH_LINE_ERR"  # taxonomy: qtyxprice mismatch
    TAX_ERR = "TAX_ERR"  # taxonomy: stated tax ≠ recomputed
    TAX_UNKNOWN_CODE = "TAX_UNKNOWN_CODE"
    TAX_MISSING_CODE = "TAX_MISSING_CODE"


@dataclass(frozen=True)
class CheckResult:
    code: ReasonCode
    severity: Severity
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "severity": self.severity.value, "detail": self.detail}


@dataclass(frozen=True)
class ValidationReport:
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """True when no ERROR-severity finding (WARNs continue the pipeline)."""
        return not any(c.severity == Severity.ERROR for c in self.checks)

    @property
    def errors(self) -> Sequence[CheckResult]:
        return tuple(c for c in self.checks if c.severity == Severity.ERROR)

    @property
    def warnings(self) -> Sequence[CheckResult]:
        return tuple(c for c in self.checks if c.severity == Severity.WARN)

    def as_dicts(self) -> list[dict[str, str]]:
        return [c.as_dict() for c in self.checks]


def validate_extraction(extraction: InvoiceExtraction) -> ValidationReport:
    """Run every deterministic check; pure function of (extraction, config)."""
    findings: list[CheckResult | None] = [
        *_check_required_fields(extraction),
        _check_currency(extraction),
        _check_iban(extraction),
        *_check_line_math(extraction),
        _check_tax(extraction),
    ]
    # Only failures land in the report: a clean extraction yields no checks.
    return ValidationReport(checks=tuple(c for c in findings if c is not None))


def _field(extraction: InvoiceExtraction, name: str) -> object | None:
    return getattr(extraction, name, None)


def _check_required_fields(extraction: InvoiceExtraction) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name in cfg.REQUIRED_ERROR_FIELDS:
        if _field(extraction, name) is None:
            results.append(
                CheckResult(
                    ReasonCode.SCHEMA_MISSING_FIELD,
                    Severity.ERROR,
                    f"missing required field: {name}",
                )
            )
    for name in cfg.REQUIRED_WARN_FIELDS:
        if _field(extraction, name) is None:
            results.append(
                CheckResult(
                    ReasonCode.SCHEMA_MISSING_FIELD,
                    Severity.WARN,
                    f"missing optional field: {name}",
                )
            )
    return results


def _check_currency(extraction: InvoiceExtraction) -> CheckResult | None:
    currency = extraction.currency
    if currency is None:
        return None  # presence handled by required-field checks
    if not (len(currency) == 3 and currency.isalpha() and currency.isupper()):
        return CheckResult(
            ReasonCode.SCHEMA_BAD_CURRENCY,
            Severity.ERROR,
            f"currency {currency!r} is not ISO-4217 shaped",
        )
    return None


def _check_iban(extraction: InvoiceExtraction) -> CheckResult | None:
    iban = extraction.iban
    if iban is None:
        return None
    normalized = iban.replace(" ", "").upper()
    country = normalized[:2]
    expected_len = cfg.IBAN_LENGTHS.get(country)
    if expected_len is None:
        return CheckResult(
            ReasonCode.SCHEMA_BAD_IBAN,
            Severity.WARN,
            f"unknown IBAN country {country!r} (table stub) — not checked",
        )
    if len(normalized) != expected_len or not normalized[2:4].isdigit():
        return CheckResult(
            ReasonCode.SCHEMA_BAD_IBAN,
            Severity.ERROR,
            f"IBAN malformed: expected length {expected_len} for {country}, got {len(normalized)}",
        )
    if _iban_mod97(normalized) != 1:
        return CheckResult(
            ReasonCode.SCHEMA_BAD_IBAN,
            Severity.ERROR,
            f"IBAN check digits fail mod-97: {normalized}",
        )
    return None


def _iban_mod97(normalized: str) -> int:
    """ISO 13616: rotate first 4 chars to the end, map letters, mod 97."""
    rearranged = normalized[4:] + normalized[:4]
    remainder = 0
    for char in rearranged:
        if char.isdigit():
            remainder = (remainder * 10 + int(char)) % 97
        elif char.isalpha():
            remainder = (remainder * 100 + (ord(char) - ord("A") + 10)) % 97
        else:
            return -1  # invalid character → fails
    return remainder


def _check_line_math(extraction: InvoiceExtraction) -> list[CheckResult]:
    results: list[CheckResult] = []
    tolerance = cfg.LINE_MATH_TOLERANCE

    for line in extraction.lines:
        if line.qty is not None and line.unit_price is not None and line.line_total is not None:
            expected = line.qty * line.unit_price
            delta = line.line_total - expected
            if abs(delta) > tolerance:
                results.append(
                    CheckResult(
                        ReasonCode.MATH_LINE_ERR,
                        Severity.ERROR,
                        f"line {line.line_no}: qty({line.qty}) x unit_price({line.unit_price}) "
                        f"= {expected}, stated line_total {line.line_total} (delta {delta:+})",
                    )
                )

    totals = [line.line_total for line in extraction.lines if line.line_total is not None]
    if extraction.total_amount is not None and totals:
        stated_sum = sum(totals, Decimal("0"))
        delta = extraction.total_amount - stated_sum
        if abs(delta) > tolerance:
            results.append(
                CheckResult(
                    ReasonCode.MATH_ERR,
                    Severity.ERROR,
                    f"Σ line_total = {stated_sum}, stated total_amount "
                    f"{extraction.total_amount} (delta {delta:+})",
                )
            )
    return results


def _check_tax(extraction: InvoiceExtraction) -> CheckResult | None:
    if extraction.total_amount is None or extraction.tax_total is None:
        return None
    lines_with_code = [
        line
        for line in extraction.lines
        if line.tax_code is not None and line.line_total is not None
    ]
    if not lines_with_code:
        return CheckResult(
            ReasonCode.TAX_MISSING_CODE,
            Severity.WARN,
            "no line tax codes present — tax not recomputed (jurisdiction stub)",
        )
    expected = sum(
        (
            (line.line_total or Decimal("0")) * cfg.TAX_RATES.get(line.tax_code or "", Decimal("0"))
            for line in lines_with_code
        ),
        Decimal("0"),
    )
    unknown = sorted(
        {
            code
            for code in (line.tax_code for line in extraction.lines)
            if code is not None and code not in cfg.TAX_RATES
        }
    )
    if unknown:
        return CheckResult(
            ReasonCode.TAX_UNKNOWN_CODE,
            Severity.WARN,
            f"unknown tax codes {unknown} — tax not recomputed (table stub)",
        )
    delta = extraction.tax_total - expected
    if abs(delta) > cfg.TAX_TOLERANCE:
        return CheckResult(
            ReasonCode.TAX_ERR,
            Severity.ERROR,
            f"recomputed tax {expected} vs stated {extraction.tax_total} (delta {delta:+})",
        )
    return None

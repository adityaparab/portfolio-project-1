"""Versioned policy configuration (issue #24, ADR 0001: rules as data).

Changing any value here changes routing outcomes, so every change is a
reviewable diff against this version string — pinned into POLICY actor
ledger entries by the graph wiring (#25).

Fail-closed semantics: a currency without a spend band cannot be
auto-approved (conservative by design — no silent FX conversion).
"""

from decimal import Decimal

VERSION = "policy@v1"

# Spend limit by currency: amounts at or below the limit are auto-approvable
# from the policy engine's perspective (the confidence gate still applies).
AUTO_APPROVE_LIMITS: dict[str, Decimal] = {
    "EUR": Decimal("2500.00"),
}

# Approval matrix: (upper bound inclusive, approver role). The first band
# covering the amount names the required approver — surfaced as evidence on
# APPROVAL_REQUIRED findings (Phase 3 assignment uses it).
APPROVAL_MATRIX: dict[str, tuple[tuple[Decimal, str], ...]] = {
    "EUR": (
        (Decimal("10000.00"), "manager"),
        (Decimal("999999999999.00"), "director"),  # sentinel upper bound
    ),
}

# A PO ordered more than STALE_PO_DAYS before the invoice issue date is
# stale (exactly STALE_PO_DAYS is still fresh — inclusive boundary).
STALE_PO_DAYS = 90

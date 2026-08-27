"""Versioned validation configuration (issue #17).

ADR 0001 discipline: tolerances and rule tables are versioned data, not code
constants — changing them is a visible, reviewable change. The version string
feeds ledger pins for validation outcomes.
"""

from decimal import Decimal

VERSION = "validation@v1"

REQUIRED_ERROR_FIELDS = ("vendor_name", "invoice_number", "issue_date", "currency", "total_amount")
REQUIRED_WARN_FIELDS = ("due_date", "iban")

# Exact-decimal arithmetic; tolerance only absorbs documented per-line rounding.
LINE_MATH_TOLERANCE = Decimal("0.00")  # off-by-a-cent FAILS (issue #17 AC)

# Tax recomputation carries its own rounding: rate x net yields fractional
# cents that real invoices round, so tax compares at one-cent tolerance
# (line math above stays exact).
TAX_TOLERANCE = Decimal("0.01")

# Jurisdiction stub: tax code -> rate. Unknown code => WARN + skip (no ERROR).
TAX_RATES: dict[str, Decimal] = {
    "S": Decimal("0.19"),  # DE standard (stub table)
    "R": Decimal("0.07"),  # reduced
    "Z": Decimal("0.00"),  # zero-rated
}

IBAN_LENGTHS: dict[str, int] = {
    # Structure table stub — extend as the corpus requires.
    "DE": 22,
    "FR": 27,
    "NL": 18,
    "GB": 22,
    "US": 34,  # treated as IBAN-like for demo purposes
}

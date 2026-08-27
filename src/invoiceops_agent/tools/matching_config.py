"""Versioned matching configuration (issue #21, ADR 0001 discipline).

Tolerance bands are data, not code: changing a band is a reviewable change
that alters routing outcomes, so it carries a version string pinned into
match ledger entries.

Boundary semantics (property-tested): a delta EXACTLY at the band limit is
WITHIN tolerance (inclusive); anything above it is a mismatch. The effective
price band is ``max(PRICE_ABS_TOLERANCE, PRICE_PCT_TOLERANCE x po_unit_price)``
— the absolute floor keeps cent-level noise on cheap items from flagging.
"""

from decimal import Decimal

VERSION = "matching@v1"

# Unit price: relative band with an absolute floor (see module docstring).
PRICE_PCT_TOLERANCE = Decimal("0.02")  # 2%
PRICE_ABS_TOLERANCE = Decimal("0.50")  # 50 cents

# Quantity: eval catalog defines QTY_MM as "invoiced > received" with no
# grace — invoicing even one unit beyond the receipt is a mismatch.
QTY_TOLERANCE = Decimal("0")

# Discrete fields (currency, UoM, vendor) compare exactly after
# casefold/strip normalization; no tolerance exists for identity.

"""Synthetic ERP generator: vendors, purchase orders, goods receipts (issue #20).

Faker-driven, seed-pinned ground truth for the 3-way match (#21) and the eval
golden set (#45). Two layers, deliberately separate:

* :func:`generate` — pure data generation (no I/O, no wall clock): same
  ``(seed, volumes, reference_date)`` ⇒ bit-identical rows. Dates are offsets
  from an injected ``reference_date`` so regeneration never drifts.
* :mod:`invoiceops_agent.data.seed_erp` — DB writer + CLI (Compose ``seed``).

Ground-truth invariants (what "clean" means — every generated row satisfies
these; the matcher/policy unit tests rely on them):

* vendor names, PO numbers, GR numbers are unique; every vendor has valid
  (mod-97) DE-shaped banking details and one home currency
* every PO has 1-4 lines; qty > 0, unit_price > 0; ``line_total = qty x
  unit_price`` exactly (Decimal); tax_code ∈ validation_config.TAX_RATES —
  the generator shares that table so clean invoices always reconcile with
  the deterministic tax check (#17)
* every PO has exactly one GR; per line ``received qty == ordered qty``
  (full receipt — partial/over receipts are eval *anomalies*, injected by
  #45's catalog, never baseline data)
* ``ordered_at ≤ received_at ≤ reference_date``; PO currency == vendor's
  home currency
* PO status mix: mostly OPEN, ~15% CLOSED; ~15% of POs ordered >90 days
  before ``reference_date`` (stale-PO ground truth for the policy engine #24)

Regeneration contract: identical within a locked Faker version (``uv.lock``);
``GENERATOR_VERSION`` is pinned into the seed ledger entry. Synthetic data
only — never real vendor names or bank details (AGENTS.md).
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from faker import Faker

from invoiceops_agent.tools.validation_config import TAX_RATES

GENERATOR_VERSION = "erp-generator@1"

DEFAULT_REFERENCE_DATE = date(2026, 9, 1)  # fixed anchor ⇒ runs never drift

CURRENCIES = ("EUR", "EUR", "EUR", "EUR", "EUR", "EUR", "EUR", "USD", "USD", "GBP")
UOM_EACH = ("EA", "PCS", "BOX")
UOM_FRACTIONAL = ("KG", "M")
CLOSED_PO_FRACTION = 0.15
STALE_PO_DAYS = 90
STALE_PO_FRACTION = 0.15
BANK_NAMES = ("Synth Bank East", "Synth Bank West", "Nordbank Synthetic", "Südliche Synthbank")


@dataclass(frozen=True)
class ErpVendor:
    name: str
    tax_id: str
    currency: str
    bank_details: dict[str, str]
    risk_flags: list[str] = field(default_factory=list)
    is_active: bool = True


@dataclass(frozen=True)
class ErpPoLine:
    line_no: str
    description: str
    qty: Decimal
    uom: str
    unit_price: Decimal
    tax_code: str

    @property
    def line_total(self) -> Decimal:
        return self.qty * self.unit_price


@dataclass(frozen=True)
class ErpPurchaseOrder:
    po_number: str
    vendor_name: str
    currency: str
    status: str  # OPEN | CLOSED
    lines: tuple[ErpPoLine, ...]
    ordered_at: date


@dataclass(frozen=True)
class ErpGoodsReceipt:
    gr_number: str
    po_number: str
    received_qty: tuple[dict[str, str | Decimal], ...]  # {line_no, qty}
    received_at: date


@dataclass(frozen=True)
class ErpDataset:
    vendors: tuple[ErpVendor, ...]
    purchase_orders: tuple[ErpPurchaseOrder, ...]
    goods_receipts: tuple[ErpGoodsReceipt, ...]
    seed: int
    reference_date: date
    generator_version: str = GENERATOR_VERSION


@dataclass(frozen=True)
class CleanInvoiceSpec:
    """A clean invoice derived from a PO (+ its full GR).

    ``extraction_dict()`` emits the JSON shape of the
    ``agents.extraction.InvoiceExtraction`` contract; tests/evals wrap it with
    ``InvoiceExtraction.model_validate`` (kept as a dict so this module never
    imports ``agents`` — boundary rules). A spec built from a generated PO
    passes every deterministic check: line math exact, tax reconciled,
    quantities equal the GR.
    """

    vendor_name: str
    invoice_number: str
    issue_date: date
    due_date: date
    currency: str
    iban: str
    po_number: str
    lines: tuple[ErpPoLine, ...]

    @property
    def net_total(self) -> Decimal:
        return sum((line.line_total for line in self.lines), Decimal("0"))

    @property
    def tax_total(self) -> Decimal:
        return sum(
            (line.line_total * TAX_RATES.get(line.tax_code, Decimal("0")) for line in self.lines),
            Decimal("0"),
        )

    def extraction_dict(self) -> dict[str, object]:
        return {
            "vendor_name": self.vendor_name,
            "invoice_number": self.invoice_number,
            "po_number": self.po_number,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "currency": self.currency,
            "total_amount": str(self.net_total),
            "tax_total": str(self.tax_total),
            "iban": self.iban,
            "lines": [
                {
                    "line_no": line.line_no,
                    "description": line.description,
                    "qty": str(line.qty),
                    "uom": line.uom,
                    "unit_price": str(line.unit_price),
                    "tax_code": line.tax_code,
                    "line_total": str(line.line_total),
                }
                for line in self.lines
            ],
        }


def generate(
    *,
    seed: int = 42,
    vendors: int = 25,
    purchase_orders: int = 75,
    reference_date: date = DEFAULT_REFERENCE_DATE,
) -> ErpDataset:
    """Deterministic dataset: same inputs ⇒ identical rows (see module docs)."""
    fake = Faker()
    Faker.seed(seed)
    rng = random.Random(seed)  # explicit RNG for all numerics — stdlib-typed

    vendor_rows: list[ErpVendor] = []
    names: set[str] = set()
    while len(vendor_rows) < vendors:
        candidate = _vendor(fake, rng)
        if candidate.name in names:  # enforce the uniqueness invariant
            continue
        names.add(candidate.name)
        vendor_rows.append(candidate)

    po_rows: list[ErpPurchaseOrder] = []
    gr_rows: list[ErpGoodsReceipt] = []
    for seq in range(1, purchase_orders + 1):
        vendor = vendor_rows[rng.randrange(len(vendor_rows))]
        po, gr = _po_with_receipt(fake, rng, seq, vendor, reference_date)
        po_rows.append(po)
        gr_rows.append(gr)

    return ErpDataset(
        vendors=tuple(vendor_rows),
        purchase_orders=tuple(po_rows),
        goods_receipts=tuple(gr_rows),
        seed=seed,
        reference_date=reference_date,
    )


def clean_invoice_for(
    po: ErpPurchaseOrder, vendor: ErpVendor, *, invoice_number: str, issue_date: date
) -> CleanInvoiceSpec:
    """Clean invoice against a generated PO and its (full) receipt."""
    return CleanInvoiceSpec(
        vendor_name=vendor.name,
        invoice_number=invoice_number,
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=30),
        currency=po.currency,
        iban=vendor.bank_details["iban"],
        po_number=po.po_number,
        lines=po.lines,
    )


# --------------------------------------------------------------------- internals


def _vendor(fake: Faker, rng: random.Random) -> ErpVendor:
    currency = CURRENCIES[rng.randrange(len(CURRENCIES))]
    risk: list[str] = []
    if rng.random() < 0.10:
        risk.append("watchlist-demo")
    return ErpVendor(
        name=str(fake.company()),
        tax_id=f"DE{rng.randrange(10**8, 10**9)}",
        currency=currency,
        bank_details={
            "iban": _de_iban(rng),
            "bank_name": BANK_NAMES[rng.randrange(len(BANK_NAMES))],
        },
        risk_flags=risk,
    )


def _po_with_receipt(
    fake: Faker, rng: random.Random, seq: int, vendor: ErpVendor, reference_date: date
) -> tuple[ErpPurchaseOrder, ErpGoodsReceipt]:
    status = "CLOSED" if rng.random() < CLOSED_PO_FRACTION else "OPEN"
    stale = rng.random() < STALE_PO_FRACTION
    if stale:
        ordered_at = reference_date - timedelta(days=rng.randint(STALE_PO_DAYS + 1, 365))
    else:
        ordered_at = reference_date - timedelta(days=rng.randint(1, STALE_PO_DAYS))

    line_count = rng.randint(1, 4)
    lines = tuple(_po_line(fake, rng, i) for i in range(1, line_count + 1))

    received_at = ordered_at + timedelta(
        days=min(rng.randint(1, 14), max((reference_date - ordered_at).days, 0))
    )
    po = ErpPurchaseOrder(
        po_number=f"PO-{reference_date.year}-{seq:05d}",
        vendor_name=vendor.name,
        currency=vendor.currency,
        status=status,
        lines=lines,
        ordered_at=ordered_at,
    )
    gr = ErpGoodsReceipt(
        gr_number=f"GR-{reference_date.year}-{seq:05d}",
        po_number=po.po_number,
        received_qty=tuple(
            {"line_no": line.line_no, "qty": line.qty} for line in lines
        ),  # full receipt — see invariants
        received_at=received_at,
    )
    return po, gr


def _po_line(fake: Faker, rng: random.Random, line_no: int) -> ErpPoLine:
    if rng.random() < 0.2:  # fractional-quantity unit of measure
        uom = UOM_FRACTIONAL[rng.randrange(len(UOM_FRACTIONAL))]
        qty = Decimal(rng.randrange(1, 500)) / Decimal("10")  # 1dp, exact math
    else:
        uom = UOM_EACH[rng.randrange(len(UOM_EACH))]
        qty = Decimal(rng.randint(1, 50))
    unit_price = Decimal(rng.randrange(100, 100_000)) / Decimal("100")  # 2dp
    tax_code = "S" if rng.random() < 0.8 else ("R" if rng.random() < 0.5 else "Z")
    return ErpPoLine(
        line_no=str(line_no),
        description=str(fake.catch_phrase())[:120],
        qty=qty,
        uom=uom,
        unit_price=unit_price,
        tax_code=tax_code,
    )


def _de_iban(rng: random.Random) -> str:
    """Valid DE IBAN: DE + check digits + 8-digit bank code + 10-digit account.

    Check digits per ISO 13616 (98 - mod97 of the zeroed rearrangement).
    """
    bban = f"{rng.randrange(10**7, 10**8):08d}{rng.randrange(10**9, 10**10):010d}"
    remainder = _mod97(f"{bban}DE00")
    check = 98 - remainder
    return f"DE{check:02d}{bban}"


def _mod97(digits: str) -> int:
    remainder = 0
    for char in digits:
        if char.isdigit():
            remainder = (remainder * 10 + int(char)) % 97
        else:
            remainder = (remainder * 100 + (ord(char) - ord("A") + 10)) % 97
    return remainder

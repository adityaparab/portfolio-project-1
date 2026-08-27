"""ERP seeder: generate + load the synthetic ERP into Postgres (issue #20).

One-shot CLI behind the Compose ``seed`` service::

    uv run python -m invoiceops_agent.data.seed_erp [--seed 42 --vendors 25 --pos 75]

Semantics (audit-aware by design):

* seeds only a *clean* database — if any vendor row exists the run aborts
  loudly rather than mixing datasets; a deterministic re-seed is a volume
  reset (``docker compose down -v``), because downstream runs/ledger rows
  are append-only and cannot be deleted (#7's guarantee cuts both ways)
* numerics are written into JSONB as exact decimal strings (``qty``,
  ``unit_price``) — the 3-way matcher (#21) parses them back with ``Decimal``
* one SYSTEM ledger entry records seed, volumes, and generator version

Usage:
    uv run python -m invoiceops_agent.data.seed_erp --help
"""

import argparse
import asyncio
import logging
import os
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from invoiceops_agent.data.erp import (
    DEFAULT_REFERENCE_DATE,
    ErpDataset,
    ErpPoLine,
    ErpVendor,
    generate,
)
from invoiceops_agent.db.models import GoodsReceipt, PurchaseOrder, Vendor
from invoiceops_agent.ledger.api import ActorType, LedgerAppend, writer
from invoiceops_agent.versions import CURRENT

logger = logging.getLogger(__name__)


class SeedError(RuntimeError):
    """Raised when the target database is not clean (or unwritable)."""


async def seed_database(engine: AsyncEngine, dataset: ErpDataset) -> dict[str, int]:
    """Load ``dataset`` into Postgres; returns row counts. Aborts if not clean."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        existing: int = (
            await session.execute(select(func.count()).select_from(Vendor))
        ).scalar_one()
        if existing:
            raise SeedError(
                f"vendors table already has {existing} rows — refusing to mix "
                "datasets. Reset the volume (docker compose down -v) for a "
                "deterministic re-seed; ledger/run history is append-only."
            )
        vendor_ids = await _insert_vendors(session, dataset.vendors)
        po_ids = await _insert_pos(session, dataset, vendor_ids)
        await _insert_grs(session, dataset, po_ids)
        await writer.append(
            session,
            LedgerAppend(
                actor_type=ActorType.SYSTEM,
                actor_id="seed",
                event={
                    "event": "erp.seeded",
                    "seed": dataset.seed,
                    "reference_date": dataset.reference_date.isoformat(),
                    "vendors": len(dataset.vendors),
                    "purchase_orders": len(dataset.purchase_orders),
                    "goods_receipts": len(dataset.goods_receipts),
                    "generator_version": dataset.generator_version,
                },
                versions=CURRENT,
            ),
        )
        await session.commit()
    counts = {
        "vendors": len(dataset.vendors),
        "purchase_orders": len(dataset.purchase_orders),
        "goods_receipts": len(dataset.goods_receipts),
    }
    logger.info("seeded %s (generator %s)", counts, dataset.generator_version)
    return counts


async def _insert_vendors(session: AsyncSession, vendors: tuple[ErpVendor, ...]) -> dict[str, int]:
    session.add_all(
        Vendor(
            name=v.name,
            tax_id=v.tax_id,
            bank_details=dict(v.bank_details),
            risk_flags=list(v.risk_flags),
            is_active=v.is_active,
        )
        for v in vendors
    )
    await session.flush()
    rows = await session.execute(select(Vendor.name, Vendor.vendor_id))
    return {name: vid for name, vid in rows.all()}


async def _insert_pos(
    session: AsyncSession, dataset: ErpDataset, vendor_ids: dict[str, int]
) -> dict[str, int]:
    session.add_all(
        PurchaseOrder(
            po_number=po.po_number,
            vendor_id=vendor_ids[po.vendor_name],
            currency=po.currency,
            status=po.status,
            lines=[_line_json(line) for line in po.lines],
            ordered_at=po.ordered_at,
        )
        for po in dataset.purchase_orders
    )
    await session.flush()
    rows = await session.execute(select(PurchaseOrder.po_number, PurchaseOrder.po_id))
    return {num: pid for num, pid in rows.all()}


def _line_json(line: ErpPoLine) -> dict[str, str]:
    """JSONB contract: numerics as exact decimal strings (Decimal ≠ JSON)."""
    return {
        "line_no": line.line_no,
        "description": line.description,
        "qty": str(line.qty),
        "uom": line.uom,
        "unit_price": str(line.unit_price),
        "tax_code": line.tax_code,
    }


async def _insert_grs(session: AsyncSession, dataset: ErpDataset, po_ids: dict[str, int]) -> None:
    session.add_all(
        GoodsReceipt(
            gr_number=gr.gr_number,
            po_id=po_ids[gr.po_number],
            received_qty=[
                {"line_no": str(entry["line_no"]), "qty": str(entry["qty"])}
                for entry in gr.received_qty
            ],
            received_at=gr.received_at,
        )
        for gr in dataset.goods_receipts
    )
    await session.flush()


async def main_async(
    dsn: str,
    *,
    seed: int = 42,
    vendors: int = 25,
    purchase_orders: int = 75,
    reference_date: date | None = None,
) -> dict[str, int]:
    from sqlalchemy.ext.asyncio import create_async_engine

    dataset = generate(
        seed=seed,
        vendors=vendors,
        purchase_orders=purchase_orders,
        reference_date=reference_date or DEFAULT_REFERENCE_DATE,
    )
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://", 1), pool_pre_ping=True
    )
    try:
        return await seed_database(engine, dataset)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: env)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--vendors", type=int, default=25)
    parser.add_argument("--pos", type=int, default=75)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # CLI config stays in the entrypoint (settings discipline applies to app
    # runtime); data/ never imports api/.
    dsn = args.dsn or os.environ.get("INVOICEOPS_DATABASE_DSN")
    if not dsn:
        parser.error("--dsn or INVOICEOPS_DATABASE_DSN is required")
    counts = asyncio.run(
        main_async(
            dsn,
            seed=args.seed,
            vendors=args.vendors,
            purchase_orders=args.pos,
            reference_date=args.reference_date,
        )
    )
    print(f"seeded: {counts}")


if __name__ == "__main__":
    main()

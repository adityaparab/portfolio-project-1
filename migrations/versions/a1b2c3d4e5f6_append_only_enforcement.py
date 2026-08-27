"""0002 append-only enforcement on ledger and decisions

Two layers (ADR 0004):
1. Triggers reject UPDATE/DELETE on both tables outright — applies to every
   role including the owner.
2. Least-privilege grants for the `invoiceops_app` login role: SELECT/INSERT
   only on ledger/decisions; full CRUD on the domain tables. Grants live here
   (not Compose init scripts) because they must postdate table creation.

Reversal = dropping the guards (owner only), itself a reviewable schema change.

Revision ID: a1b2c3d4e5f6
Revises: 822d4351d71a
Create Date: 2026-08-27

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "822d4351d71a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUARD = """
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only (ADR 0004): % forbidden on row %',
        TG_TABLE_NAME, TG_OP, OLD;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = (
    "CREATE TRIGGER {tg} BEFORE UPDATE OR DELETE ON {tbl} FOR EACH ROW EXECUTE FUNCTION {fn}();"
)

_ROLE = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'invoiceops_app') THEN
        -- Dev credential; production provisions roles externally and rotates.
        CREATE ROLE invoiceops_app LOGIN PASSWORD 'invoiceops-app';
    END IF;
END
$$;
"""


def upgrade() -> None:
    for tbl in ("ledger", "decisions"):
        fn = f"{tbl}_append_only_guard"
        op.execute(_GUARD.format(fn=fn))
        op.execute(_TRIGGER.format(tg=f"trg_{fn}", tbl=tbl, fn=fn))
        op.execute(
            f"COMMENT ON TABLE {tbl} IS "
            f"'append-only (ADR 0004); UPDATE/DELETE rejected by trigger {fn}'"
        )

    op.execute(_ROLE)
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "vendors, purchase_orders, goods_receipts, invoices, invoice_lines, "
        "runs, checkpoints, exceptions TO invoiceops_app"
    )
    op.execute("GRANT SELECT, INSERT ON ledger, decisions TO invoiceops_app")
    op.execute("GRANT SELECT ON alembic_version TO invoiceops_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO invoiceops_app")


def downgrade() -> None:
    # Role + grants are left in place (environment provisioning owns them);
    # only the trigger guards are dropped.
    for tbl in ("ledger", "decisions"):
        fn = f"{tbl}_append_only_guard"
        op.execute(f"DROP TRIGGER IF EXISTS trg_{fn} ON {tbl}")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")

"""0006 sequence grants for 0004/0005 tables

The app role's sequence grant (0002) predates dlq_entries/model_calls, so
INSERTs failed with 'permission denied for sequence' — invisible in CI
because testcontainers runs migrations as the schema owner. Grant USAGE on
ALL sequences so later tables inherit the fix too.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-28

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO invoiceops_app")


def downgrade() -> None:
    # Grant revocation is environment provisioning, not schema shape.
    pass

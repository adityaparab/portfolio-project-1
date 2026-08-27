"""0003 graph schema for LangGraph checkpointer tables

The checkpointer (langgraph-checkpoint-postgres) creates `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes` — names that collide with the domain
`checkpoints` table in `public`. Giving the saver its own schema avoids the
collision and keeps LangGraph internals out of the audited schema.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-27

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS graph")
    op.execute("GRANT USAGE ON SCHEMA graph TO invoiceops_app")
    # Tables are created later by the saver's setup() as the migrating role;
    # default privileges cover them for the app role from then on.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA graph "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO invoiceops_app"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS graph CASCADE")

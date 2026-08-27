"""0004 dead-letter queue for failed runs

issue #27: exhausted INFRA retries and deterministic BUSINESS failures land
here with their last-good-checkpoint snapshot; admin replay re-executes from
that checkpoint. Mutable by design (status PENDING -> REPLAYED/DISCARDED):
this is an ops queue, not an audit record — replay/discard transitions are
audited in the ledger instead.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-27

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dlq_entries (
            dlq_id       BIGSERIAL PRIMARY KEY,
            run_id       BIGINT NOT NULL REFERENCES runs(run_id),
            invoice_id   BIGINT NOT NULL REFERENCES invoices(invoice_id),
            node         VARCHAR(64) NOT NULL,
            failure_kind VARCHAR(16) NOT NULL,
            error_type   VARCHAR(128) NOT NULL,
            error_message TEXT NOT NULL,
            attempts     INTEGER NOT NULL DEFAULT 1,
            state_snapshot JSONB NOT NULL,
            status       VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            replayed_at  TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_dlq_status ON dlq_entries (status)")
    op.execute("CREATE INDEX ix_dlq_run ON dlq_entries (run_id)")
    # Ops queue: the app role may transition status (unlike ledger/decisions).
    op.execute("GRANT SELECT, INSERT, UPDATE ON dlq_entries TO invoiceops_app")
    op.execute(
        "COMMENT ON TABLE dlq_entries IS "
        "'dead-letter queue (issue #27): failed runs with last-good checkpoint; "
        "mutable ops queue — audit trail lives in ledger'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dlq_entries")

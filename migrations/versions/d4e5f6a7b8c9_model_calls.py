"""0005 model_calls audit trail

Reasoning + output of every LLM call, persisted for revisit/audit (Agent
Run step view). Append-only audit record like ledger/decisions: the app
role gets SELECT/INSERT only.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-28

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE model_calls (
            call_id        BIGSERIAL PRIMARY KEY,
            run_id         BIGINT REFERENCES runs(run_id),
            invoice_id     BIGINT REFERENCES invoices(invoice_id),
            stage          VARCHAR(32) NOT NULL,
            alias          VARCHAR(64) NOT NULL,
            wire_model     VARCHAR(128) NOT NULL,
            prompt_version VARCHAR(32),
            status         VARCHAR(16) NOT NULL DEFAULT 'COMPLETED',
            reasoning_text TEXT,
            output_text    TEXT NOT NULL,
            latency_ms     INTEGER,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_model_calls_run ON model_calls (run_id)")
    op.execute("CREATE INDEX ix_model_calls_invoice ON model_calls (invoice_id)")
    op.execute("GRANT SELECT, INSERT ON model_calls TO invoiceops_app")
    op.execute(
        "COMMENT ON TABLE model_calls IS "
        "'append-only audit of every LLM call: reasoning + output + model + versions'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_calls")

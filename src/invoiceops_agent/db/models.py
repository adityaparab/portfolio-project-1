"""ORM models for the ERP simulation, pipeline runs, and audit store.

Schema of record: docs/ARCHITECTURE.md §6. Money and quantities are Numeric
(never float); timestamps are timestamptz (UTC). `ledger` and `decisions` are
append-only — see migration 0002 triggers + ADR 0004.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from invoiceops_agent.db.base import Base, utcnow

EMBEDDING_DIM = 768  # nomic-embed-text / text-embedding classes; revisit if alias changes


class Vendor(Base):
    __tablename__ = "vendors"

    vendor_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    tax_id: Mapped[str | None] = mapped_column(String(64))
    bank_details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    risk_flags: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    po_id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(64), unique=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.vendor_id"))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    ordered_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    gr_id: Mapped[int] = mapped_column(primary_key=True)
    gr_number: Mapped[str] = mapped_column(String(64), unique=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.po_id"))
    received_qty: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    received_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Invoice(Base):
    __tablename__ = "invoices"

    invoice_id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.vendor_id"))
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.po_id"))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    doc_ref: Mapped[str | None] = mapped_column(Text)  # MinIO content-addressed key
    quality_tier: Mapped[str | None] = mapped_column(String(1))  # A/B/C
    currency: Mapped[str | None] = mapped_column(String(3))
    amount_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    tax_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    issue_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # Near-duplicate detection over normalized invoice content (ADR 0006 era:
    # embeddings produced via the `embed` gateway alias). HNSW chosen over
    # IVFFlat: no training step, always-fresh data at demo scale.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        Index("ix_invoices_vendor", "vendor_id"),
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_embedding", "embedding", postgresql_using="hnsw"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    line_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.invoice_id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    uom: Mapped[str | None] = mapped_column(String(16))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    tax_code: Mapped[str | None] = mapped_column(String(32))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    __table_args__ = (UniqueConstraint("invoice_id", "line_no", name="uq_invoice_line_no"),)


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.invoice_id"), index=True)
    graph_version: Mapped[str] = mapped_column(String(32))
    model_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    route: Mapped[str | None] = mapped_column(String(16))  # AUTO/EXCEPTION/REJECT
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column()


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    cp_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.run_id"), index=True)
    node: Mapped[str] = mapped_column(String(64))
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "ledger"
    # APPEND-ONLY: triggers reject UPDATE/DELETE (migration 0002, ADR 0004).

    entry_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.run_id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.invoice_id"), index=True)
    seq: Mapped[int] = mapped_column(BigInteger)  # per-run monotonic sequence
    actor_type: Mapped[str] = mapped_column(String(16))  # SYSTEM/AGENT/HUMAN/POLICY
    actor_id: Mapped[str | None] = mapped_column(String(128))
    event: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model_versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    policy_version: Mapped[str | None] = mapped_column(String(32))
    prompt_template_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_ledger_run_seq"),)


class ExceptionRecord(Base):
    __tablename__ = "exceptions"

    exception_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.invoice_id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.run_id"))
    type: Mapped[str] = mapped_column(String(32))  # taxonomy code (issue #22)
    severity: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    recommendation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assignee: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    sla_due_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Decision(Base):
    __tablename__ = "decisions"
    # APPEND-ONLY: triggers reject UPDATE/DELETE (migration 0002, ADR 0004).

    decision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.exception_id"), index=True)
    actor_user: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class DLQEntry(Base):
    """Dead-letter queue for failed runs (issue #27).

    An OPS queue, not an audit record: ``status`` mutates on replay/discard
    (unlike ledger/decisions), while every transition is still audited via
    ledger entries. ``state_snapshot`` is the last good checkpoint so admin
    replay re-executes from exactly where the run died.
    """

    __tablename__ = "dlq_entries"

    dlq_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.run_id"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.invoice_id"), index=True)
    node: Mapped[str] = mapped_column(String(64))
    failure_kind: Mapped[str] = mapped_column(String(16))  # INFRA | BUSINESS
    error_type: Mapped[str] = mapped_column(String(128))
    error_message: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=1)
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/REPLAYED/DISCARDED
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    replayed_at: Mapped[datetime | None] = mapped_column()

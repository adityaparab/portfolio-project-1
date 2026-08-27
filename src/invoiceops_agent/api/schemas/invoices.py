"""Invoice resource schemas: ingest receipt, queue page, detail aggregate."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvoiceAccepted(BaseModel):
    """201 response for POST /v1/invoices (new document accepted)."""

    model_config = ConfigDict(frozen=True)

    invoice_id: int
    run_id: int
    content_hash: str = Field(min_length=64, max_length=64)
    status: str = "RECEIVED"
    duplicate: bool = False


class QueueExceptionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    exception_id: int
    type: str
    severity: str
    status: str
    assignee: str | None = None
    sla_due_at: str | None = None  # ISO 8601 UTC
    sla_overdue_seconds: int | None = None  # aging vs. now (positive = overdue)


class QueueRunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: int
    route: str | None
    status: str
    confidence: float | None = None


class QueueItem(BaseModel):
    """One queue row: invoice denormalized read model + latest run/exception."""

    model_config = ConfigDict(frozen=True)

    invoice_id: int
    invoice_number: str | None = None
    status: str
    vendor_id: int | None = None
    currency: str | None = None
    amount_total: str | None = None  # Decimal-safe string
    issue_date: str | None = None
    created_at: str
    run: QueueRunSummary | None = None
    exception: QueueExceptionSummary | None = None


class QueuePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[QueueItem]
    total: int
    limit: int
    offset: int


class LedgerSummaryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    actor_type: str
    actor_id: str | None
    event: str  # the event name, e.g. "gate.decided"
    created_at: str
    # Provenance pins (audit/platform only — RBAC-filtered server-side)
    versions: dict[str, Any] | None = None
    policy_version: str | None = None
    prompt_template_version: str | None = None


class LedgerSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_count: int
    last_entries: list[LedgerSummaryEntry]


class ExtractionLineView(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_no: str
    description: str | None = None
    qty: str | None = None
    uom: str | None = None
    unit_price: str | None = None
    tax_code: str | None = None
    line_total: str | None = None


class ExtractionView(BaseModel):
    """Checkpointed extraction with per-field confidences (gate inputs)."""

    model_config = ConfigDict(frozen=True)

    vendor_name: str | None = None
    invoice_number: str | None = None
    po_number: str | None = None
    issue_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    total_amount: str | None = None
    tax_total: str | None = None
    iban: str | None = None
    lines: list[ExtractionLineView] = Field(default_factory=list)
    confidences: dict[str, float] = Field(default_factory=dict)
    min_confidence: float | None = None


class InvoiceDetailAggregate(BaseModel):
    """GET /v1/invoices/{id}: everything the review screens need, one call."""

    model_config = ConfigDict(frozen=True)

    invoice: QueueItem
    lines: list[ExtractionLineView] = Field(default_factory=list)
    extraction: ExtractionView | None = None
    validation: list[dict[str, Any]] = Field(default_factory=list)
    match: dict[str, Any] | None = None
    policy: list[dict[str, Any]] = Field(default_factory=list)
    gate: dict[str, Any] | None = None
    exception: dict[str, Any] | None = None  # incl. triage recommendation (#30)
    ledger: LedgerSummary
    state_available: bool = True  # False when no checkpoint survived

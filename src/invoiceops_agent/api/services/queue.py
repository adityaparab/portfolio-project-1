"""Queue read model: filterable listing + one-round-trip detail aggregate (#28).

The ``invoices`` row is the denormalized read model (the match3way node
backfills invoice_number/vendor/po/currency/amount/issue_date from the
extraction + ERP); the latest run/exception join via correlated max-id
subqueries — no N+1, fixed query count per request.

RBAC: provenance pins (model/policy/prompt versions on ledger entries) are
stripped server-side for non-audit roles — the wire never carries fields a
role must not see.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from invoiceops_agent.api.auth import PROVENANCE_ROLES, Identity
from invoiceops_agent.api.schemas.invoices import (
    ExtractionLineView,
    ExtractionView,
    InvoiceDetailAggregate,
    LedgerSummary,
    LedgerSummaryEntry,
    QueueExceptionSummary,
    QueueItem,
    QueuePage,
    QueueRunSummary,
)
from invoiceops_agent.db.models import ExceptionRecord, Invoice, LedgerEntry, Run

SORT_FIELDS = ("created_at", "amount_total", "sla_due_at", "severity")


def _latest_run_id() -> Any:
    return (
        select(func.max(Run.run_id))
        .where(Run.invoice_id == Invoice.invoice_id)
        .correlate(Invoice)
        .scalar_subquery()
    )


def _latest_exception_id() -> Any:
    return (
        select(func.max(ExceptionRecord.exception_id))
        .where(ExceptionRecord.invoice_id == Invoice.invoice_id)
        .correlate(Invoice)
        .scalar_subquery()
    )


class QueueService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # ------------------------------------------------------------------ queue

    async def list_invoices(
        self,
        *,
        status: str | None = None,
        route: str | None = None,
        exception_type: str | None = None,
        severity: str | None = None,
        assignee: str | None = None,
        vendor_id: int | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 25,
        offset: int = 0,
    ) -> QueuePage:
        latest_run = aliased(Run)
        latest_exc = aliased(ExceptionRecord)
        base = (
            select(Invoice, latest_run, latest_exc)
            .select_from(Invoice)
            .outerjoin(latest_run, latest_run.run_id == _latest_run_id())
            .outerjoin(latest_exc, latest_exc.exception_id == _latest_exception_id())
        )
        base = self._apply_filters(
            base,
            latest_run,
            latest_exc,
            status,
            route,
            exception_type,
            severity,
            assignee,
            vendor_id,
        )
        count = select(func.count()).select_from(base.subquery())

        column = self._sort_column(latest_exc, sort)
        ordered = base.order_by(
            column.desc() if order == "desc" else column.asc(), Invoice.invoice_id
        )

        async with self._sessions() as session:
            total: int = (await session.execute(count)).scalar_one()
            rows = (await session.execute(ordered.limit(limit).offset(offset))).all()
        now = datetime.now(UTC)
        return QueuePage(
            items=[
                _queue_item(invoice, run_row, exc_row, now) for invoice, run_row, exc_row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    def _apply_filters(
        self,
        stmt: Select[Any],
        latest_run: Any,
        latest_exc: Any,
        status: str | None,
        route: str | None,
        exception_type: str | None,
        severity: str | None,
        assignee: str | None,
        vendor_id: int | None,
    ) -> Select[Any]:
        if status:
            stmt = stmt.where(Invoice.status == status)
        if route:
            stmt = stmt.where(latest_run.route == route)
        if exception_type:
            stmt = stmt.where(latest_exc.type == exception_type)
        if severity:
            stmt = stmt.where(latest_exc.severity == severity)
        if assignee:
            stmt = stmt.where(latest_exc.assignee == assignee)
        if vendor_id is not None:
            stmt = stmt.where(Invoice.vendor_id == vendor_id)
        return stmt

    def _sort_column(self, latest_exc: Any, sort: str) -> Any:
        if sort == "amount_total":
            return Invoice.amount_total
        if sort == "sla_due_at":
            return latest_exc.sla_due_at
        if sort == "severity":
            return case(
                (latest_exc.severity == "CRITICAL", 4),
                (latest_exc.severity == "HIGH", 3),
                (latest_exc.severity == "MEDIUM", 2),
                (latest_exc.severity == "LOW", 1),
                else_=0,
            )
        return Invoice.created_at

    # ----------------------------------------------------------------- detail

    async def detail(
        self,
        invoice_id: int,
        identity: Identity,
        state_provider: Callable[[int], Awaitable[Any | None]],
    ) -> InvoiceDetailAggregate | None:
        async with self._sessions() as session:
            latest_run = aliased(Run)
            latest_exc = aliased(ExceptionRecord)
            row = (
                await session.execute(
                    select(Invoice, latest_run, latest_exc)
                    .select_from(Invoice)
                    .outerjoin(latest_run, latest_run.run_id == _latest_run_id())
                    .outerjoin(latest_exc, latest_exc.exception_id == _latest_exception_id())
                    .where(Invoice.invoice_id == invoice_id)
                )
            ).first()
            if row is None:
                return None
            invoice, run_row, exc_row = row
            ledger_rows = (
                (
                    await session.execute(
                        select(LedgerEntry)
                        .where(LedgerEntry.invoice_id == invoice_id)
                        .order_by(LedgerEntry.seq.desc())
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
            ledger_count: int = (
                await session.execute(
                    select(func.count()).where(LedgerEntry.invoice_id == invoice_id)
                )
            ).scalar_one()

        state = await state_provider(invoice_id)
        now = datetime.now(UTC)
        extraction_view, lines = _extraction_view(state)
        return InvoiceDetailAggregate(
            invoice=_queue_item(invoice, run_row, exc_row, now),
            lines=lines,
            extraction=extraction_view,
            validation=list(state.validation) if state else [],
            match=state.match if state else None,
            policy=list(state.policy) if state else [],
            gate=state.gate if state else None,
            exception=_exception_view(exc_row),
            ledger=self._ledger_summary(ledger_rows, ledger_count, identity),
            state_available=state is not None,
        )

    def _ledger_summary(
        self,
        rows: Sequence[LedgerEntry],
        count: int,
        identity: Identity,
    ) -> LedgerSummary:
        with_pins = identity.role in PROVENANCE_ROLES
        entries = [
            LedgerSummaryEntry(
                seq=r.seq,
                actor_type=r.actor_type,
                actor_id=r.actor_id,
                event=str(r.event.get("event", "?")),
                created_at=r.created_at.astimezone(UTC).isoformat(),
                versions=(r.model_versions or None) if with_pins else None,
                policy_version=r.policy_version if with_pins else None,
                prompt_template_version=r.prompt_template_version if with_pins else None,
            )
            for r in reversed(rows)  # chronological, last 10
        ]
        return LedgerSummary(entry_count=count, last_entries=entries)


# ---------------------------------------------------------------------- helpers


def _queue_item(invoice: Invoice, run_row: Any, exc_row: Any, now: datetime) -> QueueItem:
    run_summary: QueueRunSummary | None = None
    if run_row is not None:
        confidence: float | None = None
        if run_row.confidence is not None:
            confidence = float(run_row.confidence)
        run_summary = QueueRunSummary(
            run_id=int(run_row.run_id),
            route=run_row.route,
            status=run_row.status,
            confidence=confidence,
        )
    exc_summary: QueueExceptionSummary | None = None
    if exc_row is not None:
        overdue: int | None = None
        if exc_row.sla_due_at is not None:
            due = (
                exc_row.sla_due_at
                if exc_row.sla_due_at.tzinfo
                else exc_row.sla_due_at.replace(tzinfo=UTC)
            )
            overdue = int((now - due).total_seconds())
        exc_summary = QueueExceptionSummary(
            exception_id=int(exc_row.exception_id),
            type=exc_row.type,
            severity=exc_row.severity,
            status=exc_row.status,
            assignee=exc_row.assignee,
            sla_due_at=exc_row.sla_due_at.isoformat() if exc_row.sla_due_at else None,
            sla_overdue_seconds=overdue,
        )
    return QueueItem(
        invoice_id=invoice.invoice_id,
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        vendor_id=invoice.vendor_id,
        currency=invoice.currency,
        amount_total=str(invoice.amount_total) if invoice.amount_total is not None else None,
        issue_date=invoice.issue_date.isoformat() if invoice.issue_date else None,
        created_at=invoice.created_at.astimezone(UTC).isoformat(),
        run=run_summary,
        exception=exc_summary,
    )


def _exception_view(exc_row: Any) -> dict[str, Any] | None:
    if exc_row is None:
        return None
    return {
        "exception_id": int(exc_row.exception_id),
        "type": exc_row.type,
        "severity": exc_row.severity,
        "status": exc_row.status,
        "assignee": exc_row.assignee,
        "sla_due_at": exc_row.sla_due_at.isoformat() if exc_row.sla_due_at else None,
        "evidence": exc_row.evidence,
        "recommendation": exc_row.recommendation,
    }


def _extraction_view(state: Any) -> tuple[ExtractionView | None, list[ExtractionLineView]]:
    if state is None or not state.extraction:
        return None, []
    raw: dict[str, Any] = state.extraction
    lines = [ExtractionLineView.model_validate(line) for line in raw.get("lines", [])]
    confidences: dict[str, float] = {k: float(v) for k, v in (raw.get("confidences") or {}).items()}
    view = ExtractionView(
        vendor_name=raw.get("vendor_name"),
        invoice_number=raw.get("invoice_number"),
        po_number=raw.get("po_number"),
        issue_date=raw.get("issue_date"),
        due_date=raw.get("due_date"),
        currency=raw.get("currency"),
        total_amount=raw.get("total_amount"),
        tax_total=raw.get("tax_total"),
        iban=raw.get("iban"),
        lines=lines,
        confidences=confidences,
        min_confidence=min(confidences.values()) if confidences else None,
    )
    return view, lines

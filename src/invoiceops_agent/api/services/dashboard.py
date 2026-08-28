"""Dashboard aggregates (issue #33): computed server-side, display-only.

The frontend never recomputes metrics (AGENTS.md) — this endpoint is the
single source for Dan's screen. Fixed query count (4); cost/latency KPIs
return null until the observability stack lands (#43).
"""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.db.models import ExceptionRecord, Invoice


class DayVolume(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: str  # YYYY-MM-DD
    total: int
    auto_approved: int


class ExceptionTypeCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    severity: str
    open_count: int


class DashboardSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: str
    stp_rate: float | None  # auto-approved / processed (None before any processing)
    invoices_processed: int
    invoices_auto_approved: int
    exceptions_open: int
    aging: dict[str, int] = Field(default_factory=dict)  # on_track/over_4h/over_24h
    volume_by_day: list[DayVolume] = Field(default_factory=list)
    exception_types: list[ExceptionTypeCount] = Field(default_factory=list)
    cost_per_invoice: float | None = None  # LiteLLM spend joins in #43
    p95_latency_seconds: float | None = None  # OTel metrics land in #43


class DashboardService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def summary(self, days: int = 14) -> DashboardSummary:
        now = datetime.now(UTC)
        since = now - timedelta(days=days)
        async with self._sessions() as session:
            # STP: statuses among invoices whose runs completed routing.
            status_rows = (
                await session.execute(
                    select(Invoice.status, func.count())
                    .where(
                        Invoice.status.in_(
                            [
                                "AUTO_APPROVED",
                                "EXCEPTION",
                                "DECISION_APPROVED",
                                "RETURNED_TO_VENDOR",
                            ]
                        )
                    )
                    .group_by(Invoice.status)
                )
            ).all()
            counts = {status: int(n) for status, n in status_rows}
            auto = counts.get("AUTO_APPROVED", 0)
            human_touched = counts.get("DECISION_APPROVED", 0) + counts.get("RETURNED_TO_VENDOR", 0)
            exceptions = counts.get("EXCEPTION", 0)
            processed = auto + exceptions + human_touched

            volume_rows = (
                await session.execute(
                    select(
                        func.date_trunc("day", Invoice.created_at).label("day"),
                        func.count().label("total"),
                        func.sum(case((Invoice.status == "AUTO_APPROVED", 1), else_=0)).label(
                            "auto"
                        ),
                    )
                    .where(Invoice.created_at >= since)
                    .group_by("day")
                    .order_by("day")
                )
            ).all()

            type_rows = (
                await session.execute(
                    select(
                        ExceptionRecord.type,
                        ExceptionRecord.severity,
                        func.count().label("n"),
                        func.min(ExceptionRecord.sla_due_at).label("oldest_sla"),
                        func.max(ExceptionRecord.sla_due_at).label("newest_sla"),
                    )
                    .where(ExceptionRecord.status == "OPEN")
                    .group_by(ExceptionRecord.type, ExceptionRecord.severity)
                    .order_by(func.count().desc())
                )
            ).all()

            open_slas = (
                (
                    await session.execute(
                        select(ExceptionRecord.sla_due_at).where(
                            ExceptionRecord.status == "OPEN",
                            ExceptionRecord.sla_due_at.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

        aging = {"on_track": 0, "over_4h": 0, "over_24h": 0}
        for sla in open_slas:
            if sla is None:  # pragma: no cover — filtered in SQL
                continue
            due = sla if sla.tzinfo else sla.replace(tzinfo=UTC)
            overdue = (now - due).total_seconds()
            if overdue > 86400:
                aging["over_24h"] += 1
            elif overdue > 4 * 3600:
                aging["over_4h"] += 1
            else:
                aging["on_track"] += 1

        return DashboardSummary(
            generated_at=now.isoformat(),
            stp_rate=(auto / processed) if processed else None,
            invoices_processed=processed,
            invoices_auto_approved=auto,
            exceptions_open=int(sum(r.n for r in type_rows)),
            aging=aging,
            volume_by_day=[
                DayVolume(
                    day=row.day.date().isoformat(),
                    total=int(row.total),
                    auto_approved=int(row.auto or 0),
                )
                for row in volume_rows
            ],
            exception_types=[
                ExceptionTypeCount(type=r.type, severity=r.severity, open_count=int(r.n))
                for r in type_rows
            ],
        )

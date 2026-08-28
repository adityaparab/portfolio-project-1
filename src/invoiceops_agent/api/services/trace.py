"""Trace + provenance read services (issue #35).

Both assemble exclusively from the append-only audit surfaces — ledger,
decisions, exceptions, runs — plus the checkpointed node order. Fixed query
counts (2 and 5); reconstruction needs nothing else (README G3).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.api.schemas.trace import (
    ProvenanceDecision,
    ProvenanceException,
    ProvenancePackage,
    ProvenanceRun,
    RunTrace,
    TraceEvent,
)
from invoiceops_agent.db.models import Decision, ExceptionRecord, Invoice, LedgerEntry, Run


class TraceService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def run_trace(
        self,
        run_id: int,
        node_trace_provider: Any = None,
        stage_models: dict[str, Any] | None = None,
    ) -> RunTrace | None:
        async with self._sessions() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return None
            entries = (
                (
                    await session.execute(
                        select(LedgerEntry)
                        .where(LedgerEntry.run_id == run_id)
                        .order_by(LedgerEntry.seq)
                    )
                )
                .scalars()
                .all()
            )

        node_trace: list[str] = []
        active_node: str | None = None
        if node_trace_provider is not None:
            state, active = await node_trace_provider(run.invoice_id)
            node_trace = list(state.node_trace) if state else []
            # Blink only while the run is truly executing — a paused
            # (AWAITING_DECISION) or settled run has no live stage.
            active_node = active if run.status == "RUNNING" else None

        return RunTrace(
            run_id=run.run_id,
            invoice_id=run.invoice_id,
            status=run.status,
            route=run.route,
            confidence=float(run.confidence) if run.confidence is not None else None,
            graph_version=run.graph_version,
            node_trace=node_trace,
            active_node=active_node,
            stage_models=dict(stage_models) if stage_models else {},
            timeline=[_trace_event(e) for e in entries],
        )

    async def provenance(self, invoice_id: int) -> ProvenancePackage | None:
        async with self._sessions() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None:
                return None
            runs = (
                (await session.execute(select(Run).where(Run.invoice_id == invoice_id)))
                .scalars()
                .all()
            )
            exceptions = (
                (
                    await session.execute(
                        select(ExceptionRecord).where(ExceptionRecord.invoice_id == invoice_id)
                    )
                )
                .scalars()
                .all()
            )
            decisions = (
                (
                    await session.execute(
                        select(Decision)
                        .where(
                            Decision.exception_id.in_(
                                select(ExceptionRecord.exception_id).where(
                                    ExceptionRecord.invoice_id == invoice_id
                                )
                            )
                        )
                        .order_by(Decision.decision_id)
                    )
                )
                .scalars()
                .all()
            )
            ledger = (
                (
                    await session.execute(
                        select(LedgerEntry)
                        .where(LedgerEntry.invoice_id == invoice_id)
                        .order_by(LedgerEntry.run_id, LedgerEntry.seq)
                    )
                )
                .scalars()
                .all()
            )

        return ProvenancePackage(
            invoice_id=invoice_id,
            generated_at=datetime.now(UTC).isoformat(),
            runs=[
                ProvenanceRun(
                    run_id=r.run_id,
                    graph_version=r.graph_version,
                    model_versions=dict(r.model_versions or {}),
                    route=r.route,
                    status=r.status,
                    confidence=float(r.confidence) if r.confidence is not None else None,
                    started_at=r.started_at.astimezone(UTC).isoformat(),
                    finished_at=r.finished_at.astimezone(UTC).isoformat()
                    if r.finished_at
                    else None,
                )
                for r in runs
            ],
            exceptions=[
                ProvenanceException(
                    exception_id=e.exception_id,
                    run_id=e.run_id,
                    type=e.type,
                    severity=e.severity,
                    status=e.status,
                    evidence=dict(e.evidence or {}),
                    recommendation=dict(e.recommendation) if e.recommendation else None,
                )
                for e in exceptions
            ],
            decisions=[
                ProvenanceDecision(
                    decision_id=d.decision_id,
                    exception_id=d.exception_id,
                    actor_user=d.actor_user,
                    action=d.action,
                    rationale=d.rationale,
                    reason_code=d.reason_code,
                    created_at=d.created_at.astimezone(UTC).isoformat(),
                )
                for d in decisions
            ],
            ledger=[_trace_event(e) for e in ledger],
        )


def _trace_event(entry: LedgerEntry) -> TraceEvent:
    return TraceEvent(
        seq=entry.seq,
        actor_type=entry.actor_type,
        actor_id=entry.actor_id,
        event=str(entry.event.get("event", "?")),
        payload=dict(entry.event),
        created_at=entry.created_at.astimezone(UTC).isoformat(),
        versions=dict(entry.model_versions) if entry.model_versions else None,
        policy_version=entry.policy_version,
        prompt_template_version=entry.prompt_template_version,
    )

"""Dead-letter queue service: record failures, replay from checkpoint (#27).

Record path (called by the runner when a node ultimately fails): persist the
failure with its last-good-checkpoint snapshot, mark the run FAILED, append
a ``run.failed`` ledger entry — nothing is ever dropped silently.

Replay path (admin): claim the entry (PENDING -> REPLAYED — the idempotency
guard against double-replay), audit the claim, then hand the invoice back
to :meth:`GraphRunner.run_invoice`, which resumes from the last checkpoint.
Exactly-once comes from that checkpoint idempotency: completed nodes do not
re-execute, and a replay that fails again simply records a fresh DLQ entry.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.db.models import DLQEntry, Run
from invoiceops_agent.graph.errors import FailureKind
from invoiceops_agent.graph.retries import RetryExhausted
from invoiceops_agent.ledger.api import ActorType, LedgerAppend, writer

if TYPE_CHECKING:
    from invoiceops_agent.graph.runner import GraphRunner

logger = logging.getLogger(__name__)


class ReplayError(RuntimeError):
    pass


class DLQService:
    async def record_failure(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        run_id: int | None,
        invoice_id: int | None,
        node: str,
        exc: BaseException,
        attempts: int,
        state_snapshot: dict[str, Any],
    ) -> int | None:
        """Persist one DLQ entry + run-failed bookkeeping; returns dlq_id."""
        kind = _kind_of(exc)
        async with sessions() as session:
            entry = DLQEntry(
                run_id=run_id or 0,
                invoice_id=invoice_id or 0,
                node=node,
                failure_kind=kind.value,
                error_type=type(exc).__name__,
                error_message=str(exc)[:2000],
                attempts=attempts,
                state_snapshot=state_snapshot,
            )
            session.add(entry)
            if run_id is not None:
                run = await session.get(Run, run_id)
                if run is not None:
                    run.status = "FAILED"
            await session.flush()
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.SYSTEM,
                    actor_id="dlq",
                    run_id=run_id,
                    invoice_id=invoice_id,
                    event={
                        "event": "run.failed",
                        "node": node,
                        "failure_kind": kind.value,
                        "error_type": type(exc).__name__,
                        "attempts": attempts,
                        "dlq_id": entry.dlq_id,
                    },
                ),
            )
            await session.commit()
            logger.error(
                "run failed -> DLQ #%s (node=%s kind=%s attempts=%d)",
                entry.dlq_id,
                node,
                kind.value,
                attempts,
            )
            return entry.dlq_id

    async def replay(
        self, sessions: async_sessionmaker[AsyncSession], runner: "GraphRunner", dlq_id: int
    ) -> dict[str, Any]:
        """Claim + re-execute a dead-lettered run from its last checkpoint."""
        async with sessions() as session:
            entry = await session.get(DLQEntry, dlq_id)
            if entry is None:
                raise ReplayError(f"DLQ entry {dlq_id} not found")
            if entry.status != "PENDING":
                raise ReplayError(f"DLQ entry {dlq_id} is {entry.status}, not PENDING")
            entry.status = "REPLAYED"
            entry.replayed_at = datetime.now(UTC)
            run = await session.get(Run, entry.run_id)
            if run is not None:
                run.status = "RUNNING"  # resumes from checkpoint
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.HUMAN,
                    actor_id="dlq-replay-admin",
                    run_id=entry.run_id,
                    invoice_id=entry.invoice_id,
                    event={"event": "dlq.replayed", "dlq_id": dlq_id, "node": entry.node},
                ),
            )
            await session.commit()

        final = await runner.run_invoice(entry.invoice_id)
        return {
            "dlq_id": dlq_id,
            "invoice_id": entry.invoice_id,
            "route": final.route.value if final.route else None,
            "node_trace": final.node_trace,
        }

    async def pending(self, sessions: async_sessionmaker[AsyncSession]) -> list[dict[str, Any]]:
        async with sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(DLQEntry)
                        .where(DLQEntry.status == "PENDING")
                        .order_by(DLQEntry.dlq_id)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "dlq_id": r.dlq_id,
                    "run_id": r.run_id,
                    "invoice_id": r.invoice_id,
                    "node": r.node,
                    "failure_kind": r.failure_kind,
                    "error_type": r.error_type,
                    "attempts": r.attempts,
                }
                for r in rows
            ]


def _kind_of(exc: BaseException) -> FailureKind:
    if isinstance(exc, RetryExhausted):
        return FailureKind.INFRA
    from invoiceops_agent.graph.errors import classify

    return classify(exc)

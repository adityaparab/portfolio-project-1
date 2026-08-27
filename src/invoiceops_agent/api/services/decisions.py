"""Human-decision service: the HITL write path (issue #29).

Four-eyes (APPROVE only — the financially consequential action): the
approver must differ from the exception's assignee. Assignee is claimed on
first touch (claim-on-work), so the rule is concrete: whoever works the
exception cannot also approve it; a manager persona approves instead.

Resolution actions (APPROVE / RETURN_TO_VENDOR) resume the paused graph from
HumanReview through Archive via the checkpointer — completed nodes never
re-run. ESCALATE reassigns and keeps the exception OPEN (no resume).

Idempotency: re-submitting the SAME decision (actor + action + reason code)
after resolution returns the recorded decision, flagged as a replay; a
DIFFERENT decision after resolution is a 409.
"""

import logging
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.api.auth import Identity
from invoiceops_agent.api.schemas.decisions import (
    DecisionAction,
    DecisionConflictDetail,
    DecisionRequest,
    DecisionResponse,
)
from invoiceops_agent.db.models import Decision, ExceptionRecord, Invoice
from invoiceops_agent.ledger.api import ActorType, LedgerAppend, writer

logger = logging.getLogger(__name__)


class DecisionConflict(Exception):
    def __init__(self, detail: DecisionConflictDetail) -> None:
        super().__init__(detail.message)
        self.detail = detail


class DecisionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runner_provider: Any = None,
    ) -> None:
        self._sessions = sessions
        self._runner_provider = runner_provider  # () -> GraphRunner | None

    async def decide(
        self, exception_id: int, request: DecisionRequest, identity: Identity
    ) -> DecisionResponse:
        async with self._sessions() as session:
            exc = await session.get(ExceptionRecord, exception_id)
            if exc is None:
                raise KeyError(exception_id)
            invoice = await session.get(Invoice, exc.invoice_id)
            if invoice is None:  # pragma: no cover — FK enforced
                raise KeyError(exc.invoice_id)

            latest = await self._latest_decision(session, exception_id)

            if exc.status != "OPEN":
                return self._replay_or_conflict(latest, request, identity, exc, invoice)

            if exc.assignee is None:
                # claim-on-touch: first actor to decide owns the exception —
                # the claim persists even when the decision itself is refused
                exc.assignee = identity.user
                await session.commit()

            if request.action is DecisionAction.APPROVE and exc.assignee == identity.user:
                raise DecisionConflict(
                    DecisionConflictDetail(
                        kind="FOUR_EYES",
                        message=(
                            f"{identity.user} works this exception and cannot also "
                            "approve it (four-eyes): a different persona must approve."
                        ),
                        context={"assignee": exc.assignee, "exception_id": exception_id},
                    )
                )

            decision = Decision(
                exception_id=exception_id,
                actor_user=identity.user,
                action=request.action.value,
                rationale=request.rationale,
                reason_code=request.reason_code,
            )
            session.add(decision)
            await session.flush()

            graph_resumed = False
            if request.action is DecisionAction.ESCALATE:
                exc.assignee = request.escalate_to or "manager-queue"
            else:
                exc.status = "RESOLVED"
                invoice.status = (
                    "DECISION_APPROVED"
                    if request.action is DecisionAction.APPROVE
                    else "RETURNED_TO_VENDOR"
                )
                graph_resumed = True

            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.HUMAN,
                    actor_id=identity.user,
                    run_id=exc.run_id,
                    invoice_id=exc.invoice_id,
                    event={
                        "event": "decision.recorded",
                        "decision_id": decision.decision_id,
                        "exception_id": exception_id,
                        "action": request.action.value,
                        "reason_code": request.reason_code,
                        "rationale": request.rationale,
                        "escalated_to": request.escalate_to,
                    },
                ),
            )
            await session.commit()
            response = DecisionResponse(
                decision_id=decision.decision_id,
                exception_id=exception_id,
                invoice_id=exc.invoice_id,
                action=request.action,
                actor=identity.user,
                reason_code=request.reason_code,
                created_at=decision.created_at.astimezone(UTC).isoformat(),
                exception_status=exc.status,
                graph_resumed=graph_resumed,
            )

        if graph_resumed:
            await self._resume_graph(response.invoice_id, response.model_dump())
        return response

    async def _resume_graph(self, invoice_id: int, decision: dict[str, Any]) -> None:
        runner = self._runner_provider() if self._runner_provider else None
        if runner is None:
            logger.warning("no graph runner — decision recorded without graph resume")
            return
        await runner.submit_decision(invoice_id, decision)

    async def _latest_decision(self, session: AsyncSession, exception_id: int) -> Decision | None:
        return (
            await session.execute(
                select(Decision)
                .where(Decision.exception_id == exception_id)
                .order_by(Decision.decision_id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    def _replay_or_conflict(
        self,
        latest: Decision | None,
        request: DecisionRequest,
        identity: Identity,
        exc: ExceptionRecord,
        invoice: Invoice,
    ) -> DecisionResponse:
        same = (
            latest is not None
            and latest.actor_user == identity.user
            and latest.action == request.action.value
            and latest.reason_code == request.reason_code
        )
        if not same:
            raise DecisionConflict(
                DecisionConflictDetail(
                    kind="ALREADY_DECIDED",
                    message=(
                        f"exception {exc.exception_id} is {exc.status} by "
                        f"{latest.actor_user if latest else 'someone else'} "
                        f"({latest.action if latest else '?'}); submit a "
                        "conflicting decision is not allowed — reopen if needed."
                    ),
                    context={
                        "exception_id": exc.exception_id,
                        "status": exc.status,
                        "last_action": latest.action if latest else None,
                        "last_actor": latest.actor_user if latest else None,
                    },
                )
            )
        assert latest is not None
        return DecisionResponse(
            decision_id=latest.decision_id,
            exception_id=exc.exception_id,
            invoice_id=exc.invoice_id,
            action=DecisionAction(latest.action),
            actor=latest.actor_user,
            reason_code=latest.reason_code,
            created_at=latest.created_at.astimezone(UTC).isoformat(),
            exception_status=exc.status,
            graph_resumed=False,
            idempotent_replay=True,
        )

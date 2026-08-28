"""Model-call audit trail: writer/reader + run correlation (#live-audit).

The gateway hands every completed model round-trip to an observer; the
observer (wired in graph.runtime) persists it here, correlated to the
run/invoice/stage via a contextvar the pipeline nodes set around their
agent calls. ``model_calls`` is append-only (migration 0005): reasoning
and output are audit records, never edited after the fact.
"""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from invoiceops_agent.db.models import ModelCall


@dataclass(frozen=True)
class ModelCallRef:
    """Correlation for the currently-executing graph stage."""

    run_id: int | None
    invoice_id: int | None
    stage: str


#: Set by pipeline nodes around agent calls; read by the runtime observer.
model_call_context: ContextVar[ModelCallRef | None] = ContextVar("model_call_context", default=None)


class ModelCallView(BaseModel):
    """Read model for the API (Agent Run step view)."""

    model_config = ConfigDict(frozen=True)

    call_id: int
    run_id: int | None
    invoice_id: int | None
    stage: str
    alias: str
    wire_model: str
    prompt_version: str | None
    status: str
    reasoning_text: str | None
    output_text: str
    latency_ms: int | None
    created_at: str  # ISO 8601 UTC


class ModelCallWriter:
    async def append(
        self,
        session: AsyncSession,
        *,
        run_id: int | None,
        invoice_id: int | None,
        stage: str,
        alias: str,
        wire_model: str,
        prompt_version: str | None,
        reasoning_text: str | None,
        output_text: str,
        latency_ms: int | None,
    ) -> ModelCall:
        row = ModelCall(
            run_id=run_id,
            invoice_id=invoice_id,
            stage=stage,
            alias=alias,
            wire_model=wire_model,
            prompt_version=prompt_version,
            status="COMPLETED",
            reasoning_text=reasoning_text,
            output_text=output_text,
            latency_ms=latency_ms,
        )
        session.add(row)
        await session.flush()
        return row


class ModelCallReader:
    async def by_run(self, session: AsyncSession, run_id: int) -> list[ModelCallView]:
        rows = (
            (
                await session.execute(
                    select(ModelCall).where(ModelCall.run_id == run_id).order_by(ModelCall.call_id)
                )
            )
            .scalars()
            .all()
        )
        return [self._view(r) for r in rows]

    def _view(self, row: ModelCall) -> ModelCallView:
        from datetime import UTC

        return ModelCallView(
            call_id=row.call_id,
            run_id=row.run_id,
            invoice_id=row.invoice_id,
            stage=row.stage,
            alias=row.alias,
            wire_model=row.wire_model,
            prompt_version=row.prompt_version,
            status=row.status,
            reasoning_text=row.reasoning_text,
            output_text=row.output_text,
            latency_ms=row.latency_ms,
            created_at=row.created_at.astimezone(UTC).isoformat(),
        )


writer = ModelCallWriter()
reader = ModelCallReader()


def build_observer(
    sessions: Any, prompt_version_of: dict[str, str]
) -> Callable[[Any], Awaitable[None]]:
    """Async observer bound to a session factory; prompt_version_of maps
    stage -> prompt version for the recorded call. Failures are logged by
    the gateway (observer contract: never raise)."""

    async def observe(observation: object) -> None:
        from invoiceops_agent.gateway_client.client import ModelCallObservation

        ref = model_call_context.get()
        if ref is None:
            return  # call outside a graph stage (evals, replays) — nothing to correlate
        assert isinstance(observation, ModelCallObservation)
        async with sessions() as session:
            await writer.append(
                session,
                run_id=ref.run_id,
                invoice_id=ref.invoice_id,
                stage=ref.stage,
                alias=observation.alias,
                wire_model=observation.wire_model,
                prompt_version=prompt_version_of.get(ref.stage),
                reasoning_text=observation.reasoning,
                output_text=observation.content,
                latency_ms=observation.latency_ms,
            )
            await session.commit()

    return observe

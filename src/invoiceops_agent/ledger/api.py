"""Typed append-only audit ledger writer/reader (ADR 0004).

Writer and reader take an explicit ``AsyncSession`` — the caller owns the
transaction (data-layer convention). Entries pin actor + component versions;
``ledger`` itself rejects UPDATE/DELETE at the database level (issue #7).
"""

from collections.abc import Sequence
from datetime import UTC
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from invoiceops_agent.db.models import LedgerEntry
from invoiceops_agent.versions import CURRENT, VersionPins


class ActorType(StrEnum):
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    POLICY = "POLICY"


class LedgerAppend(BaseModel):
    """Input for one append; versions pinned by default from CURRENT."""

    model_config = ConfigDict(frozen=True)

    actor_type: ActorType
    actor_id: str
    event: dict[str, Any]
    run_id: int | None = None
    invoice_id: int | None = None
    # Explicit point-in-time pins for this entry (agents pass their prompt
    # version; policy checks pass the policy version); graph/model versions
    # default to versions.CURRENT.
    prompt_version: str | None = None
    policy_version: str | None = None
    versions: VersionPins | None = None


class LedgerEntryView(BaseModel):
    """Read model returned by the reader (API/trace endpoints consume this)."""

    model_config = ConfigDict(frozen=True)

    entry_id: int
    run_id: int | None
    invoice_id: int | None
    seq: int
    actor_type: ActorType
    actor_id: str | None
    event: dict[str, Any]
    created_at: str  # ISO 8601 UTC
    versions: dict[str, Any] = Field(default_factory=dict)
    policy_version: str | None = None
    prompt_template_version: str | None = None


class LedgerWriter:
    async def append(self, session: AsyncSession, entry: LedgerAppend) -> LedgerEntry:
        """Append one entry inside the caller's transaction.

        ``seq`` is allocated as max(seq)+1 per run; the unique constraint
        ``uq_ledger_run_seq`` guards concurrent writers (retry on conflict).
        """
        pins = entry.versions or CURRENT
        seq = await self._next_seq(session, entry.run_id)
        row = LedgerEntry(
            run_id=entry.run_id,
            invoice_id=entry.invoice_id,
            seq=seq,
            actor_type=entry.actor_type.value,
            actor_id=entry.actor_id,
            event=entry.event,
            model_versions={**pins.models, "graph": pins.graph},
            policy_version=entry.policy_version or pins.policy,
            prompt_template_version=entry.prompt_version,
        )
        session.add(row)
        await session.flush()
        return row

    async def _next_seq(self, session: AsyncSession, run_id: int | None) -> int:
        if run_id is None:
            return 1  # non-run entries (rare) are not sequenced per-run
        result = await session.execute(
            select(func.coalesce(func.max(LedgerEntry.seq), 0)).where(LedgerEntry.run_id == run_id)
        )
        current: int = int(result.scalar_one())
        return current + 1


class LedgerReader:
    async def by_run(
        self, session: AsyncSession, run_id: int, limit: int = 100, offset: int = 0
    ) -> Sequence[LedgerEntryView]:
        stmt = (
            select(LedgerEntry)
            .where(LedgerEntry.run_id == run_id)
            .order_by(LedgerEntry.seq)
            .limit(limit)
            .offset(offset)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [self._view(r) for r in rows]

    async def by_invoice(
        self, session: AsyncSession, invoice_id: int, limit: int = 100, offset: int = 0
    ) -> Sequence[LedgerEntryView]:
        stmt = (
            select(LedgerEntry)
            .where(LedgerEntry.invoice_id == invoice_id)
            .order_by(LedgerEntry.run_id, LedgerEntry.seq)
            .limit(limit)
            .offset(offset)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [self._view(r) for r in rows]

    def _view(self, row: LedgerEntry) -> LedgerEntryView:

        return LedgerEntryView(
            entry_id=row.entry_id,
            run_id=row.run_id,
            invoice_id=row.invoice_id,
            seq=row.seq,
            actor_type=ActorType(row.actor_type),
            actor_id=row.actor_id,
            event=row.event,
            created_at=row.created_at.astimezone(UTC).isoformat(),
            versions=row.model_versions or {},
            policy_version=row.policy_version,
            prompt_template_version=row.prompt_template_version,
        )


writer = LedgerWriter()
reader = LedgerReader()

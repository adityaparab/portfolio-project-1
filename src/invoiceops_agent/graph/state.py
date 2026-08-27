"""Typed graph state (Pydantic v2) — ARCHITECTURE §3.2.

Nodes return partial updates (plain dicts); LangGraph merges them into a
GraphState. Fields default permissively so old checkpoints stay replayable
when new fields are added (AGENTS.md rule).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Route(StrEnum):
    AUTO = "AUTO"
    EXCEPTION = "EXCEPTION"
    REJECT = "REJECT"


class FailureRecord(BaseModel):
    node: str
    reason_code: str
    detail: str | None = None


class GraphState(BaseModel):
    """State carried through the invoice-processing graph."""

    # Identity & document — run_id is the graph thread label; the DB rows
    # hang off invoice_id / run_db_id (set by the real ingest node, #25).
    run_id: str
    content_hash: str
    invoice_id: int | None = None
    run_db_id: int | None = None
    doc_ref: str | None = None
    duplicate: bool = False  # exact-hash dupe (API ingest rejects pre-graph)

    # Stage results (typed shapes live in tools/; dicts here stay
    # checkpoint-compatible across contract growth)
    extraction: dict[str, Any] | None = None
    validation: list[dict[str, Any]] = Field(default_factory=list)
    match: dict[str, Any] | None = None
    policy: list[dict[str, Any]] = Field(default_factory=list)

    # Gate & routing
    confidence: float | None = None
    route: Route | None = None
    gate: dict[str, Any] | None = None  # GateDecision.as_dict() (#26)

    # Exception path & human decision
    exception: dict[str, Any] | None = None
    exception_id: int | None = None
    human_decision: dict[str, Any] | None = None

    # Bookkeeping
    node_trace: list[str] = Field(default_factory=list)  # demo/observability aid
    failures: list[FailureRecord] = Field(default_factory=list)

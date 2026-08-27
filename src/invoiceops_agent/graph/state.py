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

    # Identity & document
    run_id: str
    content_hash: str
    doc_ref: str | None = None
    duplicate: bool = False  # exact-hash dupe found at ingest (#13 makes it real)

    # Stage results (filled by nodes; typed shapes land with their issues)
    extraction: dict[str, Any] | None = None
    validation: list[dict[str, Any]] = Field(default_factory=list)
    match: dict[str, Any] | None = None
    policy: list[dict[str, Any]] = Field(default_factory=list)

    # Gate & routing
    confidence: float | None = None
    route: Route | None = None

    # Exception path & human decision
    exception: dict[str, Any] | None = None
    human_decision: dict[str, Any] | None = None

    # Bookkeeping
    node_trace: list[str] = Field(default_factory=list)  # demo/observability aid
    failures: list[FailureRecord] = Field(default_factory=list)

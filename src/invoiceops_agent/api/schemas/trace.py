"""Trace + provenance schemas (issue #35)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    actor_type: str
    actor_id: str | None
    event: str
    payload: dict[str, Any]  # the full ledger event body
    created_at: str
    versions: dict[str, Any] | None = None
    policy_version: str | None = None
    prompt_template_version: str | None = None


class RunTrace(BaseModel):
    """GET /v1/runs/{run_id}/trace — the ordered node timeline."""

    model_config = ConfigDict(frozen=True)

    run_id: int
    invoice_id: int
    status: str
    route: str | None
    confidence: float | None
    graph_version: str
    node_trace: list[str] = Field(default_factory=list)  # checkpointed node order
    timeline: list[TraceEvent] = Field(default_factory=list)


class ProvenanceRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: int
    graph_version: str
    model_versions: dict[str, Any]
    route: str | None
    status: str
    confidence: float | None
    started_at: str
    finished_at: str | None


class ProvenanceException(BaseModel):
    model_config = ConfigDict(frozen=True)

    exception_id: int
    run_id: int | None
    type: str
    severity: str
    status: str
    evidence: dict[str, Any]
    recommendation: dict[str, Any] | None


class ProvenanceDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: int
    exception_id: int
    actor_user: str
    action: str
    rationale: str
    reason_code: str
    created_at: str


class ProvenancePackage(BaseModel):
    """GET /v1/invoices/{id}/provenance — every decision and the versions
    behind it; the export shape Priya's screen renders (README G3)."""

    model_config = ConfigDict(frozen=True)

    invoice_id: int
    generated_at: str
    runs: list[ProvenanceRun]
    exceptions: list[ProvenanceException]
    decisions: list[ProvenanceDecision]
    ledger: list[TraceEvent]

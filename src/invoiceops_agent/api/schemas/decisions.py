"""Decision resource schemas (issue #29)."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DecisionAction(StrEnum):
    APPROVE = "APPROVE"  # pay it — financial consequence, four-eyes applies
    RETURN_TO_VENDOR = "RETURN_TO_VENDOR"  # reject back to sender
    ESCALATE = "ESCALATE"  # reassign upward; exception stays OPEN


class DecisionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: DecisionAction
    rationale: str = Field(min_length=10, max_length=2000)  # auditable why
    reason_code: str = Field(min_length=3, max_length=64)  # e.g. PO_TOLERATED
    escalate_to: str | None = Field(default=None, max_length=128)


class DecisionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: int
    exception_id: int
    invoice_id: int
    action: DecisionAction
    actor: str
    reason_code: str
    created_at: str
    exception_status: str
    graph_resumed: bool
    idempotent_replay: bool = False


class DecisionConflictDetail(BaseModel):
    """409 payload: what collided and why (four-eyes / already-resolved)."""

    model_config = ConfigDict(frozen=True)

    kind: str  # FOUR_EYES | ALREADY_DECIDED
    message: str
    context: dict[str, Any] = Field(default_factory=dict)

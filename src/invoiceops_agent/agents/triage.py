"""Triage agent: evidence package -> classification + recommendation (#30).

The reasoning half of ExceptionTriage (ARCHITECTURE §3.3): the deterministic
findings are facts; this agent organizes them for a human — classification
cross-check, a draft recommendation with the concrete deltas, and a
suggested action. It CANNOT approve anything: suggestions are advisory, the
decision endpoint (#29) is the only write path.

Abstention is a first-class outcome (ADR 0003 discipline): the model may
classify ``NEEDS_HUMAN``, and the caller enforces a floor on agent
confidence regardless — a low-confidence classification is rewritten to an
explicit abstention rather than a fabricated verdict.
"""

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from invoiceops_agent.agents.prompts import triage_exception_v2 as triage_exception
from invoiceops_agent.gateway_client import GatewayClient
from invoiceops_agent.tools.exception_taxonomy import EVAL_CODES, TAXONOMY, ExceptionCode

logger = logging.getLogger(__name__)

NEEDS_HUMAN = "NEEDS_HUMAN"
#: Below this the classification is rewritten to an abstention (never a guess).
ABSTAIN_CONFIDENCE_FLOOR = 0.60


class TriageOutput(BaseModel):
    """Structured triage contract (prompt schema + persisted recommendation)."""

    model_config = ConfigDict(frozen=True)

    classification: str
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_action: str
    recommendation: str
    rationale: str
    evidence_cited: list[str] = Field(default_factory=list)
    abstained: bool = False
    prompt_version: str = triage_exception.PROMPT_VERSION
    config_version: str = "triage@1"

    @field_validator("classification")
    @classmethod
    def _known_classification(cls, value: str) -> str:
        allowed = {code.value for code in TAXONOMY} | {NEEDS_HUMAN}
        if value not in allowed:
            raise ValueError(f"classification {value!r} not in taxonomy or NEEDS_HUMAN")
        return value

    @field_validator("suggested_action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        allowed = {"APPROVE", "RETURN_TO_VENDOR", "ESCALATE", NEEDS_HUMAN}
        if value not in allowed:
            raise ValueError(f"suggested_action {value!r} unknown")
        return value

    def as_exception_recommendation(self) -> dict[str, Any]:
        """The shape persisted on ``exceptions.recommendation`` (consumed by
        the Exception Review screen, #32)."""
        return {
            "classification": self.classification,
            "confidence": self.confidence,
            "abstained": self.abstained,
            "suggested_action": self.suggested_action,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
            "evidence_cited": self.evidence_cited,
            "prompt_version": self.prompt_version,
            "config_version": self.config_version,
        }


def build_evidence_package(
    *,
    findings: list[dict[str, Any]],
    extraction: dict[str, Any] | None,
    match: dict[str, Any] | None,
    exception_code: str,
) -> dict[str, Any]:
    """Assemble the structured context the model reasons over — pure.

    Only salient extraction fields ride along (no full document text): the
    agent cross-checks, it does not re-extract.
    """
    salient = {
        k: (extraction or {}).get(k)
        for k in (
            "vendor_name",
            "invoice_number",
            "po_number",
            "issue_date",
            "currency",
            "total_amount",
            "tax_total",
        )
    }
    lines = (extraction or {}).get("lines") or []
    return {
        "exception_code": exception_code,  # deterministic primary code (precedence)
        "taxonomy": [
            {"code": code.value, "severity": TAXONOMY[code].severity.value}
            for code in TAXONOMY
            if code in EVAL_CODES or code is ExceptionCode.APPROVAL_REQUIRED
        ],
        "findings": findings,
        "match_outcome": (match or {}).get("outcome"),
        "match_deltas": [
            f.get("delta") for f in ((match or {}).get("findings") or []) if f.get("delta")
        ],
        "invoice": salient,
        "invoice_lines": [
            {k: line.get(k) for k in ("line_no", "qty", "unit_price", "line_total")}
            for line in lines[:10]
        ],
    }


class TriageAgent:
    ALIAS = "triage-reasoner"

    def __init__(self, gateway: GatewayClient) -> None:
        self._gateway = gateway

    async def triage(
        self, evidence: dict[str, Any], *, scenario: str | None = None
    ) -> TriageOutput:
        """Model call only — the caller owns persistence + ledger (#25 node)."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": triage_exception.SYSTEM},
            {
                "role": "user",
                "content": triage_exception.USER_TEMPLATE.format(
                    evidence=json.dumps(evidence, indent=2, default=str)
                ),
            },
        ]
        result = await self._gateway.complete(self.ALIAS, messages, TriageOutput, scenario=scenario)
        if not isinstance(result, TriageOutput):  # pragma: no cover — type-narrow
            raise TypeError("gateway returned unstructured result for triage")
        return self._enforce_abstention(result)

    @staticmethod
    def _enforce_abstention(output: TriageOutput) -> TriageOutput:
        """Model-side abstention is honored as-is; low-confidence verdicts are
        rewritten to explicit abstentions — never a fabricated classification."""
        if output.classification == NEEDS_HUMAN:
            return output.model_copy(update={"abstained": True})
        if output.confidence < ABSTAIN_CONFIDENCE_FLOOR:
            logger.info(
                "triage confidence %.2f below floor — abstention enforced",
                output.confidence,
            )
            return output.model_copy(
                update={
                    "abstained": True,
                    "classification": NEEDS_HUMAN,
                    "suggested_action": NEEDS_HUMAN,
                }
            )
        return output.model_copy(update={"abstained": False})

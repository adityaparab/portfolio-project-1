"""Pure routing functions — table-driven unit-testable (ARCHITECTURE §3).

These are the only place edge decisions live; nodes never decide routes.
tau comes from the versioned gate config (#26) — one source of truth,
tunable via eval sweep in Phase 5.
"""

from typing import Any

from invoiceops_agent.graph.state import GraphState
from invoiceops_agent.tools import gate_config


def route_after_ingest(state: GraphState) -> str:
    """Duplicates never enter the pipeline (issue #13)."""
    return "reject" if state.duplicate else "extract"


def route_after_validate(state: GraphState) -> str:
    """Hard validation errors go to triage; soft ones continue."""
    if _has_failure(state.validation, severity="ERROR"):
        return "exception_triage"
    return "match3way"


def route_after_match(state: GraphState) -> str:
    match = state.match or {}
    if match.get("outcome") == "MISMATCH":
        return "exception_triage"
    return "policy"


def route_after_policy(state: GraphState) -> str:
    if _has_failure(state.policy, severity="HIGH"):
        return "exception_triage"
    return "gate"


def route_after_gate(state: GraphState) -> str:
    """Abstention rule (ADR 0003): below tau ALWAYS triage; conf == tau is AUTO."""
    conf = state.confidence
    if conf is None or conf < gate_config.TAU:
        return "exception_triage"
    return "auto_approve"


def _has_failure(results: list[dict[str, Any]], severity: str) -> bool:
    return any(r.get("severity") == severity for r in results)

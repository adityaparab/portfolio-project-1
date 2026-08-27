"""Stub node implementations (hello-path, issue #8).

Each node returns a partial state update. Real logic lands with its own
issue: extract #16, validate #17, match3way #22, policy #25, gate #26,
triage #30. Stubs produce deterministic, inspectable transitions only.
"""

from typing import Any

from invoiceops_agent.graph.state import GraphState

NodeResult = dict[str, Any]


def _visit(state: GraphState, node: str) -> NodeResult:
    return {"node_trace": [*state.node_trace, node]}


def ingest(state: GraphState) -> NodeResult:
    return _visit(state, "ingest")


def extract(state: GraphState) -> NodeResult:
    # Placeholder until the extraction agent (#16): trivial field shape.
    return {**_visit(state, "extract"), "extraction": {"vendor": "stub", "lines": 1}}


def validate(state: GraphState) -> NodeResult:
    return {**_visit(state, "validate"), "validation": []}


def match3way(state: GraphState) -> NodeResult:
    return {**_visit(state, "match3way"), "match": {"outcome": "MATCH", "deltas": []}}


def policy(state: GraphState) -> NodeResult:
    return {**_visit(state, "policy"), "policy": []}


def gate(state: GraphState) -> NodeResult:
    # Stub confidence high enough to exercise the AUTO path; #26 computes the
    # real composite and #27 tunes τ via eval.
    return {**_visit(state, "gate"), "confidence": 0.9}


def auto_approve(state: GraphState) -> NodeResult:
    return {**_visit(state, "auto_approve"), "route": "AUTO"}


def exception_triage(state: GraphState) -> NodeResult:
    return {
        **_visit(state, "exception_triage"),
        "route": "EXCEPTION",
        "exception": {"type": "STUB"},
    }


def human_review(state: GraphState) -> NodeResult:
    return _visit(state, "human_review")


def archive(state: GraphState) -> NodeResult:
    return _visit(state, "archive")


def reject(state: GraphState) -> NodeResult:
    return {**_visit(state, "reject"), "route": "REJECT"}

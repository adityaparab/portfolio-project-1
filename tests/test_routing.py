"""Table-driven unit tests for the pure routing functions (issue #8 AC)."""

import pytest

from invoiceops_agent.graph import routing
from invoiceops_agent.graph.state import GraphState, Route
from invoiceops_agent.tools import gate_config


def _state(**kw: object) -> GraphState:
    return GraphState(run_id="r1", content_hash="h1", **kw)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_state(duplicate=True), "reject"),
        (_state(duplicate=False), "extract"),
    ],
)
def test_route_after_ingest(state: GraphState, expected: str) -> None:
    assert routing.route_after_ingest(state) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_state(validation=[{"severity": "ERROR"}]), "exception_triage"),
        (_state(validation=[{"severity": "WARN"}]), "match3way"),
        (_state(validation=[]), "match3way"),
    ],
)
def test_route_after_validate(state: GraphState, expected: str) -> None:
    assert routing.route_after_validate(state) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_state(match={"outcome": "MISMATCH"}), "exception_triage"),
        (_state(match={"outcome": "MATCH"}), "policy"),
        (_state(match={"outcome": "TOLERABLE"}), "policy"),
        (_state(match=None), "policy"),
    ],
)
def test_route_after_match(state: GraphState, expected: str) -> None:
    assert routing.route_after_match(state) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_state(policy=[{"severity": "HIGH"}]), "exception_triage"),
        (_state(policy=[{"severity": "MEDIUM"}]), "gate"),
        (_state(policy=[]), "gate"),
    ],
)
def test_route_after_policy(state: GraphState, expected: str) -> None:
    assert routing.route_after_policy(state) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_state(confidence=gate_config.TAU), "auto_approve"),  # boundary: == τ is AUTO
        (_state(confidence=gate_config.TAU - 0.0001), "exception_triage"),
        (_state(confidence=None), "exception_triage"),  # abstention (ADR 0003)
        (_state(confidence=1.0), "auto_approve"),
    ],
)
def test_route_after_gate_boundary(state: GraphState, expected: str) -> None:
    assert routing.route_after_gate(state) == expected


@pytest.mark.unit
def test_route_enum_values_match_spec() -> None:
    assert {r.value for r in Route} == {"AUTO", "EXCEPTION", "REJECT"}

"""Unit tests for the composite confidence gate (issue #26): the §3.5
formula, per-term monotonicity, tau boundary, fail-safe missing inputs."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from invoiceops_agent.tools import gate_config as cfg
from invoiceops_agent.tools.confidence_gate import (
    GateInputs,
    GateRoute,
    confidence,
    decide,
    min_critical_confidence,
    policy_severity_term,
)

pytestmark = pytest.mark.unit

GOOD_CONF = {
    "vendor_name": 0.99,
    "invoice_number": 0.98,
    "issue_date": 0.99,
    "currency": 0.99,
    "total_amount": 0.99,
    "tax_total": 0.98,
    "iban": 0.99,
    "line[1].qty": 0.99,
    "line[1].unit_price": 0.98,
    "line[1].line_total": 0.99,
    "due_date": 0.10,  # low-risk: must not drag the minimum
}


def test_clean_high_confidence_run_reaches_auto() -> None:
    decision = decide(GateInputs(GOOD_CONF, normalized_match_delta=0.0))
    assert decision.route is GateRoute.AUTO_APPROVE
    assert decision.confidence >= cfg.TAU


def test_low_critical_confidence_escalates() -> None:
    conf = dict(GOOD_CONF, total_amount=0.3)  # money field tanks the score
    decision = decide(GateInputs(conf, normalized_match_delta=0.0))
    assert decision.route is GateRoute.ESCALATE


def test_min_critical_confidence_ignores_low_risk_fields() -> None:
    assert min_critical_confidence(GOOD_CONF) == 0.98
    noisy = dict(GOOD_CONF, due_date=0.0, phone=0.0)
    assert min_critical_confidence(noisy) == 0.98


def test_no_confidences_fails_safe_to_zero() -> None:
    assert min_critical_confidence({}) == 0.0
    assert confidence(GateInputs({}, normalized_match_delta=0.0)) == pytest.approx(
        cfg.W_MATCH * 1.0 + cfg.W_POLICY * 1.0
    )


def test_missing_match_input_costs_the_whole_term() -> None:
    with_match = confidence(GateInputs(GOOD_CONF, normalized_match_delta=0.0))
    without = confidence(GateInputs(GOOD_CONF, normalized_match_delta=None))
    assert without == pytest.approx(with_match - cfg.W_MATCH)


def test_exact_tau_routes_auto_below_routes_exception() -> None:
    # conf exactly tau -> AUTO (documented boundary); one epsilon below -> EXCEPTION
    field_term = 1.0
    target_conf = cfg.TAU
    # solve match delta so the composite lands exactly on tau
    delta = 1.0 - (target_conf - cfg.W_FIELD * field_term - cfg.W_POLICY) / cfg.W_MATCH
    inputs = GateInputs({k: 1.0 for k in GOOD_CONF}, normalized_match_delta=round(delta, 12))
    assert confidence(inputs) == pytest.approx(target_conf, abs=1e-9)
    assert decide(inputs).route is GateRoute.AUTO_APPROVE

    below = GateInputs({k: 1.0 for k in GOOD_CONF}, normalized_match_delta=round(delta + 0.01, 12))
    assert decide(below).route is GateRoute.ESCALATE


@given(
    field=st.floats(min_value=0.0, max_value=1.0),
    delta=st.floats(min_value=0.0, max_value=1.0),
)
def test_monotonic_in_field_confidence_and_match(field: float, delta: float) -> None:
    """Each term strictly (weakly) increases confidence in isolation."""
    base = GateInputs({"total_amount": field}, normalized_match_delta=delta)
    up_field = GateInputs({"total_amount": min(1.0, field + 0.01)}, normalized_match_delta=delta)
    up_match = GateInputs({"total_amount": field}, normalized_match_delta=max(0.0, delta - 0.01))
    assert confidence(up_field) >= confidence(base)
    assert confidence(up_match) >= confidence(base)


@given(
    field=st.floats(min_value=0.05, max_value=1.0),
    delta=st.floats(min_value=0.05, max_value=1.0),
)
def test_confidence_bounded_unit_interval(field: float, delta: float) -> None:
    score = confidence(GateInputs({"total_amount": field}, normalized_match_delta=delta))
    assert 0.0 <= score <= 1.0


def test_policy_severity_term_penalizes_and_floors() -> None:
    assert policy_severity_term(()) == 1.0
    assert policy_severity_term(["MEDIUM"]) == pytest.approx(0.7)
    assert policy_severity_term(["MEDIUM", "LOW"]) == pytest.approx(0.6)
    assert policy_severity_term(["CRITICAL", "HIGH", "MEDIUM", "LOW"]) == 0.0  # floored
    assert policy_severity_term(["WEIRD"]) == pytest.approx(0.7)  # unknown -> MEDIUM


def test_decision_records_inputs_and_versions() -> None:
    decision = decide(
        GateInputs(GOOD_CONF, normalized_match_delta=0.25, policy_severities=("MEDIUM",))
    )
    as_dict = decision.as_dict()
    assert as_dict["config_version"] == cfg.VERSION
    assert as_dict["tau"] == cfg.TAU
    assert as_dict["terms"]["match"] == 0.75
    assert as_dict["terms"]["policy"] == 0.7
    assert as_dict["terms"]["field"] == pytest.approx(0.98)
    assert as_dict["weights"] == {"field": 0.4, "match": 0.4, "policy": 0.2}


def test_weights_are_normalized_and_tau_documented() -> None:
    assert pytest.approx(1.0) == cfg.W_FIELD + cfg.W_MATCH + cfg.W_POLICY
    assert 0.0 < cfg.TAU < 1.0

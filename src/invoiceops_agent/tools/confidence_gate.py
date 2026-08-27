"""Composite confidence gate — the abstention mechanism (issue #26, ADR 0003).

A composite score, never a raw model logit: extraction confidence (w1) +
match closeness (w2) + policy cleanliness (w3), compared to tau. Below tau
the invoice ALWAYS goes to triage — the system abstains rather than guess.

Pure module: no I/O, no clock. The gate node (#25 wiring) records
``GateDecision.as_dict()`` in the ledger; routing reads ``TAU`` from the
versioned config (same constant, one source of truth).

Boundary semantics: confidence exactly tau routes AUTO (>= comparison);
anything below routes EXCEPTION. Missing inputs fail safe — each absent
term contributes 0, never a free pass.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from invoiceops_agent.tools import gate_config as cfg


class GateRoute(StrEnum):
    AUTO_APPROVE = "AUTO"
    ESCALATE = "EXCEPTION"


@dataclass(frozen=True)
class GateInputs:
    """Everything the formula consumes, already normalized by the caller.

    ``field_confidences`` — extraction per-field confidences (header keys
    and ``line[<no>].<field>`` keys). ``normalized_match_delta`` — the
    matcher's [0,1] drift score (1 = fully off); None = no match evidence.
    ``policy_severities`` — severity strings of surviving policy findings.
    """

    field_confidences: Mapping[str, float]
    normalized_match_delta: float | None = None
    policy_severities: Sequence[str] = ()


@dataclass(frozen=True)
class GateDecision:
    confidence: float
    route: GateRoute
    tau: float
    terms: dict[str, float] = field(default_factory=dict)  # w-term breakdown
    weights: dict[str, float] = field(default_factory=dict)  # pinned config weights
    config_version: str = cfg.VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "route": self.route.value,
            "tau": self.tau,
            "terms": self.terms,
            "weights": self.weights,
            "config_version": self.config_version,
        }


def min_critical_confidence(confidences: Mapping[str, float]) -> float:
    """Minimum confidence over money/payment-critical fields only.

    Low-risk fields (due date, phone, …) are excluded by design — a bad read
    there must not tank the gate (ARCHITECTURE §3.5). No critical field
    present (empty extraction) fails safe to 0.0.
    """
    keys = [k for k in confidences if _is_critical(k)]
    if not keys:
        return 0.0
    return min(max(0.0, confidences[k]) for k in keys)  # clamp per key, then min


def _is_critical(key: str) -> bool:
    if key in cfg.CRITICAL_FIELDS:
        return True
    if key.startswith("line["):
        field = key.rsplit(".", 1)[-1]
        return field in cfg.CRITICAL_LINE_FIELDS
    return False


def policy_severity_term(severities: Sequence[str]) -> float:
    """1 - sum(penalties), floored at 0 — findings only ever reduce it."""
    penalty = sum(cfg.SEVERITY_PENALTY.get(s, 0.3) for s in severities)
    return max(0.0, 1.0 - penalty)


def confidence(inputs: GateInputs) -> float:
    """The composite score in [0, 1] (weights fixed by versioned config)."""
    field_term = min_critical_confidence(inputs.field_confidences)
    match_term = (
        1.0 - inputs.normalized_match_delta if inputs.normalized_match_delta is not None else 0.0
    )
    policy_term = policy_severity_term(inputs.policy_severities)
    score = cfg.W_FIELD * field_term + cfg.W_MATCH * match_term + cfg.W_POLICY * policy_term
    return min(1.0, max(0.0, score))


def decide(inputs: GateInputs, tau: float = cfg.TAU) -> GateDecision:
    """Score + route. conf >= tau → AUTO (exact-tau routes AUTO, documented);
    anything below → EXCEPTION (abstention, ADR 0003)."""
    score = confidence(inputs)
    route = GateRoute.AUTO_APPROVE if score >= tau else GateRoute.ESCALATE
    return GateDecision(
        confidence=score,
        route=route,
        tau=tau,
        terms={
            "field": round(min_critical_confidence(inputs.field_confidences), 6),
            "match": (
                round(1.0 - inputs.normalized_match_delta, 6)
                if inputs.normalized_match_delta is not None
                else 0.0
            ),
            "policy": round(policy_severity_term(inputs.policy_severities), 6),
        },
        weights={
            "field": cfg.W_FIELD,
            "match": cfg.W_MATCH,
            "policy": cfg.W_POLICY,
        },
    )

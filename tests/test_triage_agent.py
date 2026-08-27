"""Unit tests for the triage agent (issue #30): cassette scenarios,
abstention discipline, evidence assembly."""

import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from invoiceops_agent.agents.triage import (
    ABSTAIN_CONFIDENCE_FLOOR,
    NEEDS_HUMAN,
    TriageAgent,
    TriageOutput,
    build_evidence_package,
)
from invoiceops_agent.gateway_client import CassetteStore, GatewayClient

pytestmark = pytest.mark.unit

PRICE_EVIDENCE: dict[str, Any] = {
    "exception_code": "PRICE_MM",
    "findings": [
        {
            "code": "PRICE_MM",
            "severity": "ERROR",
            "detail": "line 1: unit price 110.00 exceeds PO 100.00 by +10.00",
            "delta": {"invoice": "110.00", "po": "100.00", "delta": "10.00", "band": "2"},
        }
    ],
    "match_outcome": "MISMATCH",
    "invoice": {
        "vendor_name": "Acme Supplies GmbH",
        "invoice_number": "INV-1",
        "po_number": "PO-2026-00001",
        "currency": "EUR",
        "total_amount": "1100.00",
    },
}


def _output(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "classification": "PRICE_MM",
        "confidence": 0.9,
        "suggested_action": "ESCALATE",
        "recommendation": "Check whether the 10% price increase was agreed; PO says 100.00.",
        "rationale": "Unit price exceeds the PO beyond the 2% band.",
        "evidence_cited": ["PRICE_MM"],
    }
    base.update(overrides)
    return base


def _agent(tmp_path: Path, scenario: str, payload: dict[str, Any]) -> TriageAgent:
    cassettes = CassetteStore(root=tmp_path / f"cass-{scenario}")
    cassettes.save("triage-reasoner", scenario, "h", json.dumps(payload))
    gateway = GatewayClient(
        base_url="http://gateway.invalid",
        api_key="sk-test",
        cassette_store=cassettes,
        cassette_mode="replay",
    )
    return TriageAgent(gateway)


def test_price_mismatch_classifies_with_recommendation(tmp_path: Path) -> None:
    agent = _agent(tmp_path, "price-mm", _output())
    result = _run(agent.triage(PRICE_EVIDENCE, scenario="price-mm"))
    assert result.classification == "PRICE_MM"
    assert result.abstained is False
    assert "100.00" in result.recommendation
    rec = result.as_exception_recommendation()
    assert rec["prompt_version"] == "triage@v1"
    assert rec["suggested_action"] == "ESCALATE"
    assert rec["evidence_cited"] == ["PRICE_MM"]


def test_missing_po_scenario(tmp_path: Path) -> None:
    evidence = dict(
        PRICE_EVIDENCE,
        exception_code="MISSING_PO",
        findings=[{"code": "MISSING_PO", "severity": "ERROR", "detail": "no PO reference"}],
    )
    agent = _agent(
        tmp_path,
        "missing-po",
        _output(
            classification="MISSING_PO",
            suggested_action="RETURN_TO_VENDOR",
            recommendation="Ask the vendor to reference a valid PO.",
            rationale="No PO reference on the document.",
            evidence_cited=["MISSING_PO"],
        ),
    )
    result = _run(agent.triage(evidence, scenario="missing-po"))
    assert result.classification == "MISSING_PO"


def test_model_abstention_is_preserved(tmp_path: Path) -> None:
    agent = _agent(
        tmp_path,
        "abstain",
        _output(
            classification=NEEDS_HUMAN,
            confidence=0.55,
            suggested_action=NEEDS_HUMAN,
            recommendation="Evidence conflicts; a human should reconcile the PO.",
            rationale="Two findings point in different directions.",
            evidence_cited=["PRICE_MM"],
        ),
    )
    result = _run(agent.triage(PRICE_EVIDENCE, scenario="abstain"))
    assert result.abstained is True
    assert result.classification == NEEDS_HUMAN


def test_low_confidence_verdict_is_rewritten_to_abstention(tmp_path: Path) -> None:
    """Model guessed PRICE_MM at 0.4 confidence -> explicit abstention, never
    a fabricated classification (the enforced floor)."""
    agent = _agent(tmp_path, "unsure", _output(confidence=0.4))
    result = _run(agent.triage(PRICE_EVIDENCE, scenario="unsure"))
    assert result.abstained is True
    assert result.classification == NEEDS_HUMAN
    assert result.suggested_action == NEEDS_HUMAN
    assert ABSTAIN_CONFIDENCE_FLOOR == 0.60


def test_unknown_classification_rejected_by_schema() -> None:
    with pytest.raises(Exception, match="classification"):
        TriageOutput.model_validate(_output(classification="TOTAL_GARBAGE"))


def test_evidence_package_carries_deltas_not_documents() -> None:
    extraction: dict[str, Any] = {
        "vendor_name": "Acme",
        "invoice_number": "INV-1",
        "po_number": "PO-1",
        "issue_date": "2026-08-14",
        "currency": "EUR",
        "total_amount": "110.00",
        "tax_total": "20.90",
        "iban": "DE02x",  # must NOT ride along
        "lines": [{"line_no": "1", "qty": "1", "unit_price": "110", "line_total": "110"}],
    }
    match: dict[str, Any] = {
        "outcome": "MISMATCH",
        "findings": [{"code": "PRICE_MM", "delta": {"po": "100.00", "delta": "10.00"}}],
    }
    package = build_evidence_package(
        findings=[{"code": "PRICE_MM", "severity": "ERROR", "detail": "over band"}],
        extraction=extraction,
        match=match,
        exception_code="PRICE_MM",
    )
    assert package["exception_code"] == "PRICE_MM"
    assert package["match_deltas"] == [{"po": "100.00", "delta": "10.00"}]
    assert "iban" not in package["invoice"]
    assert package["invoice_lines"][0]["unit_price"] == "110"
    codes = {entry["code"] for entry in package["taxonomy"]}
    assert "DUP_EXACT" in codes and "APPROVAL_REQUIRED" in codes


def _run(coro: Coroutine[Any, Any, TriageOutput]) -> TriageOutput:
    import asyncio

    return asyncio.run(coro)

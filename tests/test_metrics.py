"""Unit tests for eval metrics (issue #19) — pure comparison + F1 rules."""

from typing import Any

import pytest

from invoiceops_agent.eval.metrics import (
    FieldTally,
    money_match,
    normalize_text,
    tally_documents,
    text_match,
)


@pytest.mark.unit
def test_text_match_normalizes() -> None:
    assert text_match("  ACME   GmbH ", "acme gmbh")
    assert not text_match("Acme Inc", "Acme GmbH")
    assert not text_match(None, "Acme")


@pytest.mark.unit
def test_money_match_tolerance_and_garbage() -> None:
    assert money_match("149.99", "149.990")
    assert money_match(149.99, "150.00")  # exactly at ±0.01
    assert not money_match("149.99", "150.02")
    assert not money_match("N/A", "150.00")
    assert not money_match(None, "150.00")


@pytest.mark.unit
def test_normalize_text() -> None:
    assert normalize_text("  Foo   Bar ") == "foo bar"
    assert normalize_text("   ") is None
    assert normalize_text(None) is None


@pytest.mark.unit
def test_tally_counts_tp_fp_fn() -> None:
    docs: list[dict[str, Any]] = [
        {  # both match
            "extraction": {"vendor_name": "Acme GmbH"},
            "labels": {"vendor_name": "acme gmbh"},
        },
        {  # wrong value → FP + FN
            "extraction": {"vendor_name": "Wrong"},
            "labels": {"vendor_name": "Acme"},
        },
        {  # escalation (None) → FN only, no FP
            "extraction": None,
            "labels": {"vendor_name": "Acme"},
        },
    ]
    tallies = tally_documents(docs)
    t = tallies["vendor_name"]
    assert (t.tp, t.fp, t.fn) == (1, 1, 2)
    assert t.support == 3
    assert t.f1 == pytest.approx(2 * 1 / (2 * 1 + 1 + 2))


@pytest.mark.unit
def test_unlabeled_fields_skipped() -> None:
    docs: list[dict[str, Any]] = [
        {"extraction": {"tax_total": 5.0}, "labels": {"vendor_name": "X"}},
    ]
    tallies = tally_documents(docs)
    assert tallies["tax_total"].support == 0  # no label → not counted
    assert tallies["vendor_name"].fn == 1  # extraction missed the label


@pytest.mark.unit
def test_field_tally_zero_denominator() -> None:
    assert FieldTally().f1 == 0.0

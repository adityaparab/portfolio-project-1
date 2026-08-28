"""Unit checks on ORM metadata: table inventory matches ARCHITECTURE §6."""

import pytest

from invoiceops_agent.db import Base


@pytest.mark.unit
def test_metadata_covers_all_spec_tables() -> None:
    expected = {
        "vendors",
        "purchase_orders",
        "goods_receipts",
        "invoices",
        "invoice_lines",
        "runs",
        "checkpoints",
        "ledger",
        "exceptions",
        "decisions",
        "dlq_entries",
        "model_calls",
    }
    assert expected == set(Base.metadata.tables.keys())


@pytest.mark.unit
def test_critical_constraints_present() -> None:
    invoices = Base.metadata.tables["invoices"]
    assert invoices.c.content_hash.unique  # dedupe guarantee (issue #13 relies on it)
    ledger = Base.metadata.tables["ledger"]
    assert any(c.name == "uq_ledger_run_seq" for c in ledger.constraints)
    assert "embedding" in invoices.c  # pgvector near-dup column

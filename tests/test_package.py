"""Skeleton smoke tests: package layout and version contract."""

import pytest

import invoiceops_agent


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert invoiceops_agent.__version__ == "0.1.0"


@pytest.mark.unit
def test_boundary_subpackages_importable() -> None:
    from invoiceops_agent import agents, api, gateway_client, graph, ledger, obs, tools

    assert agents is not None
    assert api is not None
    assert gateway_client is not None
    assert graph is not None
    assert ledger is not None
    assert obs is not None
    assert tools is not None

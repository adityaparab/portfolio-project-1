"""Thin OpenAI-SDK wrapper over the LiteLLM proxy — the single doorway for
model calls (ADR 0005): virtual aliases, guardrails, budgets, cassettes."""

from invoiceops_agent.gateway_client.cassettes import CassetteStore
from invoiceops_agent.gateway_client.client import GatewayClient
from invoiceops_agent.gateway_client.errors import (
    GatewayBudgetError,
    GatewayConfigError,
    GatewayError,
    GatewayGuardrailError,
    GatewayResponseError,
    GatewayTransportError,
)
from invoiceops_agent.gateway_client.guardrails import Redactor, apply_guardrails

__all__ = [
    "CassetteStore",
    "GatewayBudgetError",
    "GatewayClient",
    "GatewayConfigError",
    "GatewayError",
    "GatewayGuardrailError",
    "GatewayResponseError",
    "GatewayTransportError",
    "Redactor",
    "apply_guardrails",
]

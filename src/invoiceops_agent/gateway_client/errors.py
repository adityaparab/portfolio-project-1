"""Typed gateway exceptions — every failure mode has a name (issue #15)."""

from typing import Any


class GatewayError(Exception):
    """Base class for gateway failures."""

    def __init__(self, message: str, *, alias: str, detail: Any = None) -> None:
        super().__init__(message)
        self.alias = alias
        self.detail = detail


class GatewayConfigError(GatewayError):
    """Unknown alias or misconfiguration — caller bug, never retried."""


class GatewayBudgetError(GatewayError):
    """Per-call token budget exceeded — rejected before spending."""


class GatewayGuardrailError(GatewayError):
    """Outbound content failed a guardrail (PII/injection) — never sent."""


class GatewayResponseError(GatewayError):
    """Malformed/unvalidatable output after structured retries — escalate."""


class GatewayTransportError(GatewayError):
    """Infra failure (timeout/5xx) after bounded retries — retried with backoff."""

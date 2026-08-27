"""Node-boundary error classification (issue #27, ARCHITECTURE §3.4).

Only INFRA failures retry — transport, timeouts, DB connectivity: transient
by nature. BUSINESS failures (deterministic bad output, config bugs,
routing invariants) never retry: re-running deterministic code on the same
input yields the same failure, and blind retries would only duplicate cost.
"""

from enum import StrEnum

from invoiceops_agent.gateway_client.errors import (
    GatewayBudgetError,
    GatewayConfigError,
    GatewayGuardrailError,
    GatewayResponseError,
    GatewayTransportError,
)


class FailureKind(StrEnum):
    INFRA = "INFRA"  # transient — retried with backoff, DLQ when exhausted
    BUSINESS = "BUSINESS"  # deterministic — never retried, straight to DLQ


# Transient infrastructure failures (retryable).
_INFRA_TYPES: tuple[type[BaseException], ...] = (
    GatewayTransportError,
    ConnectionError,
    TimeoutError,
    OSError,  # includes socket errors from stores/fetches
)


def classify(exc: BaseException) -> FailureKind:
    """Classify by exception type; unknown exceptions fail safe to INFRA
    (retry-then-DLQ) because an unclassified crash must never be silent."""
    if isinstance(
        exc, (GatewayResponseError, GatewayGuardrailError, GatewayConfigError, GatewayBudgetError)
    ):
        return FailureKind.BUSINESS
    if isinstance(exc, _INFRA_TYPES):
        return FailureKind.INFRA
    if _has_cause_kind(exc):
        return FailureKind.INFRA  # SQLAlchemy OperationalError & friends
    # ValueError / KeyError / TypeError from node logic: deterministic bugs.
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return FailureKind.BUSINESS
    return FailureKind.INFRA  # unknown ⇒ retried, then DLQ'd — never silent


def _has_cause_kind(exc: BaseException) -> bool:
    for cause in exc.__cause__, exc.__context__:
        if isinstance(cause, BaseException) and cause is not exc and classify_shallow(cause):
            return True
    return False


def classify_shallow(exc: BaseException) -> bool:
    """True when the exception (or a nested DB driver error by name) is
    infra-shaped — SQLAlchemy wraps driver exceptions in OperationalError."""
    name = type(exc).__name__
    return name in ("OperationalError", "InterfaceError", "DisconnectionError") or isinstance(
        exc, _INFRA_TYPES
    )

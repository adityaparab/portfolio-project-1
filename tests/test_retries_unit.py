"""Unit tests: error classification + retry policy (issue #27)."""

from collections.abc import Coroutine
from typing import Any

import pytest

from invoiceops_agent.gateway_client.errors import (
    GatewayBudgetError,
    GatewayConfigError,
    GatewayGuardrailError,
    GatewayResponseError,
    GatewayTransportError,
)
from invoiceops_agent.graph.errors import FailureKind, classify
from invoiceops_agent.graph.retries import RetryExhausted, RetryPolicy, with_retries

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (GatewayTransportError("timeout", alias="x"), FailureKind.INFRA),
        (ConnectionError("db gone"), FailureKind.INFRA),
        (TimeoutError("slow"), FailureKind.INFRA),
        (OSError("socket"), FailureKind.INFRA),
        (GatewayResponseError("garbage output", alias="x"), FailureKind.BUSINESS),
        (GatewayGuardrailError("pii", alias="x"), FailureKind.BUSINESS),
        (GatewayConfigError("bad alias", alias="x"), FailureKind.BUSINESS),
        (GatewayBudgetError("over budget", alias="x"), FailureKind.BUSINESS),
        (ValueError("routing bug"), FailureKind.BUSINESS),
        (KeyError("missing"), FailureKind.BUSINESS),
        (RuntimeError("unknown"), FailureKind.INFRA),  # fail safe: retried, then DLQ
    ],
)
def test_classification(exc: Exception, expected: FailureKind) -> None:
    assert classify(exc) is expected


def test_policy_delay_is_exponential_capped_with_jitter() -> None:
    policy = RetryPolicy(attempts=5, base_delay=0.5, max_delay=4.0, jitter=0.1)
    assert policy.delay_for(1, roll=0.0) == 0.5
    assert policy.delay_for(2, roll=0.0) == 1.0
    assert policy.delay_for(3, roll=0.0) == 2.0
    assert policy.delay_for(4, roll=0.0) == 4.0  # 4.0 raw == cap
    assert policy.delay_for(5, roll=0.0) == 4.0  # 8.0 capped
    assert policy.delay_for(1, roll=1.0) == pytest.approx(0.6)  # jitter added


class _Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)


async def _ok() -> str:
    return "done"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    import asyncio

    return asyncio.run(coro)


def test_infra_flake_is_survived_without_human_action() -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise GatewayTransportError("flake", alias="extract-vision")
        return "done"

    sleeper = _Sleeper()
    policy = RetryPolicy(attempts=3, base_delay=0.1, max_delay=1.0, jitter=0.0)
    result = _run(with_retries("extract", flaky, policy, sleep=sleeper.sleep))
    assert result == "done"
    assert calls["n"] == 3
    assert sleeper.delays == [0.1, 0.2]  # exponential backoff between attempts


def test_business_failure_never_retries() -> None:
    calls = {"n": 0}

    async def bad() -> str:
        calls["n"] += 1
        raise GatewayResponseError("deterministic garbage", alias="extract-vision")

    sleeper = _Sleeper()
    policy = RetryPolicy(attempts=3, base_delay=0.1, jitter=0.0)

    def go() -> Any:
        return with_retries("extract", bad, policy, sleep=sleeper.sleep)

    with pytest.raises(GatewayResponseError):
        _run(go())
    assert calls["n"] == 1  # zero retries — deterministic outcome
    assert sleeper.delays == []


def test_exhausted_infra_raises_retry_exhausted() -> None:
    calls = {"n": 0}

    async def always_down() -> str:
        calls["n"] += 1
        raise GatewayTransportError("down", alias="extract-vision")

    policy = RetryPolicy(attempts=3, base_delay=0.0, jitter=0.0)

    def go() -> Any:
        return with_retries("extract", always_down, policy, sleep=_Sleeper().sleep)

    with pytest.raises(RetryExhausted) as excinfo:
        _run(go())
    assert excinfo.value.attempts == 3
    assert excinfo.value.node == "extract"
    assert isinstance(excinfo.value.last, GatewayTransportError)
    assert calls["n"] == 3


def test_retry_exhausted_is_never_rewrapped() -> None:
    async def raises_exhausted() -> str:
        raise RetryExhausted("extract", 3, RuntimeError("inner"))

    async def noop(delay: float) -> None: ...

    policy = RetryPolicy(attempts=2)
    with pytest.raises(RetryExhausted):
        _run(with_retries("gate", raises_exhausted, policy, sleep=noop))

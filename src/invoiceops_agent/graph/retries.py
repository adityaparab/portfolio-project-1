"""Per-node retry policy: exponential backoff + jitter, INFRA only (issue #27).

``with_retries`` wraps one node execution: INFRA failures retry up to
``attempts`` times with ``base x 2^(n-1)`` backoff (capped, + jitter);
BUSINESS failures raise immediately (deterministic outcomes — ARCHITECTURE
§3.4). Sleep and randomness are injected so tests are fast and exact.

Caps come from settings (env-tunable): see api.settings graph_retry_*.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from invoiceops_agent.graph.errors import FailureKind, classify

logger = logging.getLogger(__name__)


class RetryExhausted(Exception):
    """INFRA failures exhausted the retry budget. Carries the last error and
    the attempt count for the DLQ record."""

    def __init__(self, node: str, attempts: int, last: BaseException) -> None:
        super().__init__(f"node {node!r}: infra failure persisted after {attempts} attempts")
        self.node = node
        self.attempts = attempts
        self.last = last


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25

    def delay_for(self, attempt: int, roll: float) -> float:
        """Backoff for the pause after ``attempt`` (1-based): exponential,
        capped, plus uniform jitter in [0, jitter]."""
        capped: float = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return float(round(capped + roll * self.jitter, 4))


async def with_retries[T](
    node: str,
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = lambda: random.uniform(0.0, 1.0),
) -> T:
    """Execute ``fn`` under the retry policy.

    BUSINESS errors propagate on attempt 1; INFRA errors retry; exhaustion
    raises :class:`RetryExhausted` (classified INFRA by the DLQ path).
    """
    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return await fn()
        except RetryExhausted:
            raise  # never re-retry an exhaustion wrapper
        except Exception as exc:
            kind = classify(exc)
            last = exc
            if kind is FailureKind.BUSINESS:
                logger.warning(
                    "node %s failed (BUSINESS, no retry): %s: %s",
                    node,
                    type(exc).__name__,
                    exc,
                )
                exc.graph_node = node  # type: ignore[attr-defined]  # DLQ node attribution
                raise
            if attempt >= policy.attempts:
                break
            delay = policy.delay_for(attempt, rng())
            logger.warning(
                "node %s infra failure on attempt %d/%d, retrying in %.3fs: %s",
                node,
                attempt,
                policy.attempts,
                delay,
                exc,
            )
            await sleep(delay)
    raise RetryExhausted(node, policy.attempts, last or RuntimeError("unreachable"))


def retrying(
    node: str, fn: Callable[[Any], Awaitable[Any]], policy: RetryPolicy
) -> Callable[[Any], Awaitable[Any]]:
    """Wrap a node callable for graph assembly (builder applies this)."""

    async def run(state: Any) -> Any:
        return await with_retries(node, lambda: fn(state), policy)

    return run

"""Node runtime: the dependency bundle real nodes receive (issue #25).

Nodes stay pure-ish functions of (state, context); every external service —
DB sessions, object store, gateway, near-dup service, clock — is injected
here so tests can substitute fakes and the graph never constructs
infrastructure itself. Builders take primitives, not the API settings
module — graph must not import upward across the boundary.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.agents.extraction import ExtractionAgent
from invoiceops_agent.agents.prompts import extract_invoice_v3, triage_exception_v2
from invoiceops_agent.agents.triage import TriageAgent
from invoiceops_agent.gateway_client.client import CallObserver, GatewayClient
from invoiceops_agent.storage.minio import ObjectStore
from invoiceops_agent.tools.near_dup import Embedder, GatewayEmbedder, NearDupService


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class NodeContext:
    sessions: async_sessionmaker[AsyncSession]
    store: ObjectStore
    gateway: GatewayClient
    extraction_agent: ExtractionAgent
    near_dup: NearDupService
    # None => basic package without recommendation (degraded, still reaches human)
    triage_agent: TriageAgent | None = None
    clock: Callable[[], datetime] = utc_now
    # Cassette scenario for replay/record runs (ADR 0007); None = live calls.
    gateway_scenario: str | None = None


def build_gateway(
    *,
    base_url: str,
    api_key: str,
    token_budgets: dict[str, int] | None = None,
    timeout_seconds: float = 120.0,
    infra_retries: int = 2,
    alias_model_map: dict[str, str] | None = None,
    call_observer: CallObserver | None = None,
) -> GatewayClient:
    return GatewayClient(
        base_url=base_url,
        api_key=api_key,
        token_budgets=token_budgets,
        timeout_seconds=timeout_seconds,
        infra_retries=infra_retries,
        alias_model_map=alias_model_map or {},
        call_observer=call_observer,
    )


def build_context(
    *,
    sessions: async_sessionmaker[AsyncSession],
    store: ObjectStore,
    gateway: GatewayClient,
    embedder: Embedder | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> NodeContext:
    """Assemble the node context from live services.

    ``embedder`` defaults to the gateway embedder (production); tests pass
    the deterministic :class:`~invoiceops_agent.tools.near_dup.HashEmbedder`.
    """
    near_dup = NearDupService(embedder or GatewayEmbedder(gateway))
    gateway.attach_call_observer(build_model_call_observer(sessions))
    return NodeContext(
        sessions=sessions,
        store=store,
        gateway=gateway,
        extraction_agent=ExtractionAgent(store=store, gateway=gateway, session_factory=sessions),
        triage_agent=TriageAgent(gateway),
        near_dup=near_dup,
        clock=clock,
    )


#: stage -> prompt version pinned on recorded model calls (agents own these).
STAGE_PROMPT_VERSIONS = {
    "extract": extract_invoice_v3.PROMPT_VERSION,
    "triage": triage_exception_v2.PROMPT_VERSION,
}


def build_model_call_observer(sessions: async_sessionmaker[AsyncSession]) -> CallObserver:
    """DB writer for the gateway audit hook (reasoning + output per call)."""
    from invoiceops_agent.ledger.model_calls import build_observer

    return build_observer(sessions, STAGE_PROMPT_VERSIONS)

"""Node runtime: the dependency bundle real nodes receive (issue #25).

Nodes stay pure-ish functions of (state, context); every external service —
DB sessions, object store, gateway, near-dup service, clock — is injected
here so tests can substitute fakes and the graph never constructs
infrastructure itself.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from invoiceops_agent.agents.extraction import ExtractionAgent
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.gateway_client import GatewayClient
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
    clock: Callable[[], datetime] = utc_now
    # Cassette scenario for replay/record runs (ADR 0007); None = live calls.
    gateway_scenario: str | None = None


def build_gateway(settings: Settings) -> GatewayClient:
    """Gateway client from settings (alias map, budgets, retries)."""
    return GatewayClient(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        token_budgets=settings.gateway_token_budgets,
        timeout_seconds=settings.gateway_timeout_seconds,
        infra_retries=settings.gateway_infra_retries,
        alias_model_map=json.loads(settings.gateway_model_map_json or "{}"),
    )


def build_context(
    settings: Settings,
    *,
    gateway: GatewayClient,
    sessions: async_sessionmaker[AsyncSession],
    store: ObjectStore,
    embedder: Embedder | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> NodeContext:
    """Assemble the node context from live services.

    ``embedder`` defaults to the gateway embedder (production); tests pass
    the deterministic :class:`~invoiceops_agent.tools.near_dup.HashEmbedder`.
    """
    near_dup = NearDupService(embedder or GatewayEmbedder(gateway))
    return NodeContext(
        sessions=sessions,
        store=store,
        gateway=gateway,
        extraction_agent=ExtractionAgent(store=store, gateway=gateway, session_factory=sessions),
        near_dup=near_dup,
        clock=clock,
    )

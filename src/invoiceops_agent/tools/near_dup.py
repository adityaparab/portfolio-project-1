"""Near-duplicate detection over pgvector embeddings (issue #23).

Catches altered resubmissions (DUP_NEAR: small edits — date/total +-0.5% —
of an already-seen invoice) that survive the exact content-hash dedupe
(#13) because their bytes differ. Complements, never replaces, it.

Pipeline: normalized salient fields -> embedding -> pgvector cosine
similarity against prior invoices (HNSW index ``ix_invoices_embedding``).

Two embedding providers:

* :class:`GatewayEmbedder` — the ``embed`` gateway alias (nomic-embed class;
  the only production path — all model traffic goes through the gateway).
* :class:`HashEmbedder` — deterministic hashing-trick fallback (token ->
  768-dim feature hashing, unit-normalized). No network, no model: used in
  unit/integration tests and as a safe degradation when no gateway is
  configured. Word-level token overlap gives it the locality the real
  embeddings have: a one-token edit barely moves the cosine; different
  invoices collapse toward zero.

Determinism: both providers are pure functions of the normalized text.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from invoiceops_agent.db.models import EMBEDDING_DIM, Invoice
from invoiceops_agent.gateway_client import GatewayClient
from invoiceops_agent.tools import near_dup_config as cfg


@dataclass(frozen=True)
class NearDupHit:
    invoice_id: int
    similarity: float


@dataclass(frozen=True)
class NearDupOutcome:
    """Result of checking one invoice against the corpus.

    ``hits`` is empty when no prior invoice reaches the threshold; the
    caller (graph policy wiring, #25) turns hits into a DUP_NEAR exception
    finding with these as evidence.
    """

    invoice_id: int
    hits: tuple[NearDupHit, ...]
    threshold: float
    config_version: str = cfg.VERSION


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class GatewayEmbedder:
    """Production path: ``embed`` alias through the gateway (ADR 0005)."""

    ALIAS = "embed"

    def __init__(self, gateway: GatewayClient) -> None:
        self._gateway = gateway

    async def embed(self, text: str) -> list[float]:
        vector = await self._gateway.embed(self.ALIAS, text)
        _check_dim(vector)
        return vector


class HashEmbedder:
    """Deterministic fallback: feature-hashing token counts, unit norm.

    Same text => identical vector; a one-token edit changes one bucket, so
    cosine similarity tracks token overlap (the locality tests pin this).
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    async def embed(self, text: str) -> list[float]:
        _check_dim_basis(self._dim)
        vec = [0.0] * self._dim
        for token in _tokens(text):
            bucket = (
                int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
                % self._dim
            )
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            raise ValueError("cannot embed empty text")
        return [v / norm for v in vec]


class NearDupService:
    def __init__(
        self, embedder: Embedder, *, threshold: float = cfg.NEAR_DUP_SIMILARITY_THRESHOLD
    ) -> None:
        self._embedder = embedder
        self._threshold = threshold

    async def check_and_store(
        self, session: AsyncSession, invoice_id: int, text: str
    ) -> NearDupOutcome:
        """Similarity search against the corpus, then store the invoice's own
        embedding (after the query — the invoice never matches itself).

        Caller owns the transaction (data-layer convention).
        """
        vector = await self._embedder.embed(text)
        hits = await self.find_similar(session, invoice_id, vector)
        await self.store_embedding(session, invoice_id, vector)
        return NearDupOutcome(invoice_id=invoice_id, hits=tuple(hits), threshold=self._threshold)

    async def find_similar(
        self, session: AsyncSession, invoice_id: int, vector: list[float]
    ) -> list[NearDupHit]:
        """Prior invoices with cosine similarity >= threshold, best first.

        Boundary: similarity exactly at the threshold is NOT a hit (strict
        distance comparison — see near_dup_config docstring).
        """
        _check_dim(vector)
        max_distance = 1.0 - self._threshold
        distance = Invoice.embedding.cosine_distance(vector)
        stmt = (
            select(Invoice.invoice_id, (1.0 - distance).label("similarity"))
            .where(
                Invoice.invoice_id != invoice_id,
                Invoice.embedding.is_not(None),
                distance < max_distance,
            )
            .order_by(distance)
            .limit(cfg.TOP_K)
        )
        rows = await session.execute(stmt)
        return [NearDupHit(int(r.invoice_id), float(r.similarity)) for r in rows.all()]

    async def store_embedding(
        self, session: AsyncSession, invoice_id: int, vector: list[float]
    ) -> None:
        _check_dim(vector)
        await session.execute(
            update(Invoice).where(Invoice.invoice_id == invoice_id).values(embedding=vector)
        )


def salient_text(invoice: dict[str, Any]) -> str:
    """Canonical text over the fields that define invoice identity.

    Normalization: casefold + whitespace collapse per field; Decimals in
    plain string form; missing fields render as ``field:unknown`` so two
    invoices missing the SAME field stay similar (missing ≠ different).
    """
    parts: list[str] = []
    for name in (
        "vendor_name",
        "invoice_number",
        "issue_date",
        "due_date",
        "currency",
        "total_amount",
        "tax_total",
        "iban",
    ):
        value = invoice.get(name)
        parts.append(f"{name}:{_norm_value(value)}")
    for i, line in enumerate(invoice.get("lines") or []):
        for field in ("description", "qty", "unit_price", "line_total"):
            parts.append(f"line{i}.{field}:{_norm_value(line.get(field))}")
    return " ".join(parts)


def _norm_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, Decimal):
        return str(value.normalize())
    return " ".join(str(value).casefold().split())


def _tokens(text: str) -> list[str]:
    return [t for t in text.casefold().split() if t]


def _check_dim(vector: Sequence[float]) -> None:
    if len(vector) != EMBEDDING_DIM:
        raise ValueError(
            f"embedding dimension {len(vector)} != schema dimension {EMBEDDING_DIM} "
            "(did the gateway alias change? bump EMBEDDING_DIM via migration)"
        )


def _check_dim_basis(dim: int) -> None:
    if dim <= 0:
        raise ValueError("embedding dimension must be positive")

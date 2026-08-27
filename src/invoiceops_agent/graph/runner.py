"""Graph runner: execute the wired pipeline for one invoice (issues #25/#27).

Checkpoint-driven: a finished thread returns its final state (idempotent),
a crashed/paused thread resumes from its last checkpoint with
``ainvoke(None)`` — completed nodes never re-run — and a fresh thread
starts normally. Any ultimate failure (business on attempt 1, infra after
the retry budget) lands in the DLQ with its last-good-checkpoint snapshot
and a ``run.failed`` ledger entry — nothing is dropped silently.

The API kicks this as a background task after ingest; eval (#45) and the
DLQ replay admin call it directly.
"""

import logging
from typing import Any

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from invoiceops_agent.graph.builder import compile_graph
from invoiceops_agent.graph.dlq import DLQService
from invoiceops_agent.graph.nodes.pipeline import PipelineNodes
from invoiceops_agent.graph.retries import RetryExhausted, RetryPolicy
from invoiceops_agent.graph.runtime import NodeContext
from invoiceops_agent.graph.state import GraphState

logger = logging.getLogger(__name__)


class GraphRunner:
    def __init__(
        self,
        context: NodeContext,
        checkpointer: BaseCheckpointSaver[Any],
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._ctx = context
        self._dlq = DLQService()
        self._graph = compile_graph(
            checkpointer=checkpointer, nodes=PipelineNodes(context), retry_policy=retry_policy
        )

    async def run_invoice(self, invoice_id: int) -> GraphState:
        """Start, resume, or return the pipeline run for one invoice."""
        config: RunnableConfig = {"configurable": {"thread_id": self.thread(invoice_id)}}

        snapshot = await self._graph.aget_state(config)
        if snapshot.next:
            # Unfinished business: crashed or paused mid-graph — continue
            # from the last checkpoint; completed nodes do not re-run.
            logger.info(
                "resuming invoice_id=%s from checkpoint (next=%s)", invoice_id, snapshot.next
            )
            return await self._execute(None, config, invoice_id)
        if snapshot.values:
            # Finished thread: idempotent replay of the final state.
            return GraphState.model_validate(snapshot.values)
        state = GraphState(run_id=self.thread(invoice_id), content_hash="", invoice_id=invoice_id)
        return await self._execute(state, config, invoice_id)

    @staticmethod
    def thread(invoice_id: int) -> str:
        return f"invoice-{invoice_id}"

    async def _execute(
        self, state: GraphState | None, config: RunnableConfig, invoice_id: int
    ) -> GraphState:
        try:
            result = await self._graph.ainvoke(state, config)
            return GraphState.model_validate(result)
        except Exception as exc:
            node, attempts = _failure_meta(exc)
            snapshot = await self._snapshot(config)
            await self._dlq.record_failure(
                self._ctx.sessions,
                run_id=(snapshot or {}).get("run_db_id"),
                invoice_id=invoice_id,
                node=node,
                exc=exc,
                attempts=attempts,
                state_snapshot=dict(snapshot or {"invoice_id": invoice_id}),
            )
            raise

    async def _snapshot(self, config: RunnableConfig) -> dict[str, Any] | None:
        try:
            state = await self._graph.aget_state(config)
            return dict(state.values) if state.values else None
        except Exception:
            return None


def _failure_meta(exc: BaseException) -> tuple[str, int]:
    """(failing node, attempts) for the DLQ record."""
    if isinstance(exc, RetryExhausted):
        return exc.node, exc.attempts
    # Business failures raise on attempt 1; the retry wrapper tags the node.
    node = getattr(exc, "graph_node", "unknown")
    return str(node), 1

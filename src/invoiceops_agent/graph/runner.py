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
            checkpointer=checkpointer,
            nodes=PipelineNodes(context),
            retry_policy=retry_policy,
            interrupt_after=("exception_triage",),  # HITL pause (#29)
        )

    async def run_invoice(self, invoice_id: int) -> GraphState:
        """Start, resume, or return the pipeline run for one invoice."""
        config: RunnableConfig = {"configurable": {"thread_id": self.thread(invoice_id)}}

        snapshot = await self._graph.aget_state(config)
        if snapshot.next:
            if self._paused_for_human(snapshot):
                # Waiting on a HITL decision — only submit_decision() resumes.
                return GraphState.model_validate(snapshot.values)
            # Crashed mid-graph: continue from the last checkpoint; completed
            # nodes do not re-run.
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

    @staticmethod
    def _paused_for_human(snapshot: Any) -> bool:
        return any(getattr(task, "interrupts", ()) for task in getattr(snapshot, "tasks", ()))

    async def submit_decision(self, invoice_id: int, decision: dict[str, Any]) -> GraphState:
        """Record the human decision into the paused thread and resume it
        through HumanReview -> Archive (issue #29)."""
        config: RunnableConfig = {"configurable": {"thread_id": self.thread(invoice_id)}}
        await self._graph.aupdate_state(config, {"human_decision": decision})
        return await self._execute(None, config, invoice_id)

    async def state_for(self, invoice_id: int) -> GraphState | None:
        """Final (or last-good) checkpointed state — the read model for the
        detail aggregate (#28). None when the thread has no checkpoints."""
        config: RunnableConfig = {"configurable": {"thread_id": self.thread(invoice_id)}}
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception:
            logger.warning("state unavailable for invoice_id=%s", invoice_id, exc_info=True)
            return None
        if not snapshot.values:
            return None
        try:
            return GraphState.model_validate(snapshot.values)
        except Exception:
            logger.warning("checkpoint state for invoice_id=%s failed validation", invoice_id)
            return None

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

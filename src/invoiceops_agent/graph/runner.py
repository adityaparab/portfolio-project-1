"""Graph runner: execute the wired pipeline for one invoice (issue #25).

Owns the compiled graph + checkpointer. ``run_invoice`` is checkpoint-driven:
a finished thread returns its final state (idempotent), a crashed/paused
thread resumes from its last checkpoint with ``ainvoke(None)`` (LangGraph
thread persistence — the #25 resume AC), and a fresh thread starts normally.

The API kicks this as a background task after ingest; eval (#45) and the
DLQ replay (#27) call it directly.
"""

import logging
from typing import Any

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from invoiceops_agent.graph.builder import compile_graph
from invoiceops_agent.graph.nodes.pipeline import PipelineNodes
from invoiceops_agent.graph.runtime import NodeContext
from invoiceops_agent.graph.state import GraphState

logger = logging.getLogger(__name__)


class GraphRunner:
    def __init__(self, context: NodeContext, checkpointer: BaseCheckpointSaver[Any]) -> None:
        self._ctx = context
        self._graph = compile_graph(checkpointer=checkpointer, nodes=PipelineNodes(context))

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
            result = await self._graph.ainvoke(None, config)
        elif snapshot.values:
            # Finished thread: idempotent replay of the final state.
            return GraphState.model_validate(snapshot.values)
        else:
            state = GraphState(
                run_id=self.thread(invoice_id), content_hash="", invoice_id=invoice_id
            )
            result = await self._graph.ainvoke(state, config)
        return GraphState.model_validate(result)

    @staticmethod
    def thread(invoice_id: int) -> str:
        return f"invoice-{invoice_id}"

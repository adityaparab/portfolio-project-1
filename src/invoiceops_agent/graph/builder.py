"""Graph assembly: topology per ARCHITECTURE §3.1.

ingest → extract → validate → match3way → policy → gate
    ↓(dupe)  ↓(hard err)  ↓(mismatch)  ↓(policy fail)  ↓(conf < τ)
  reject                exception_triage → human_review → archive
                                      auto_approve → archive
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from invoiceops_agent.graph import routing
from invoiceops_agent.graph.nodes import stubs
from invoiceops_agent.graph.state import GraphState


def build_graph() -> StateGraph[GraphState]:
    g = StateGraph(GraphState)

    g.add_node("ingest", stubs.ingest)
    g.add_node("extract", stubs.extract)
    g.add_node("validate", stubs.validate)
    g.add_node("match3way", stubs.match3way)
    g.add_node("policy", stubs.policy)
    g.add_node("gate", stubs.gate)
    g.add_node("auto_approve", stubs.auto_approve)
    g.add_node("exception_triage", stubs.exception_triage)
    g.add_node("human_review", stubs.human_review)
    g.add_node("archive", stubs.archive)
    g.add_node("reject", stubs.reject)

    g.add_edge(START, "ingest")
    g.add_conditional_edges(
        "ingest", routing.route_after_ingest, {"reject": "reject", "extract": "extract"}
    )
    g.add_edge("extract", "validate")
    g.add_conditional_edges(
        "validate",
        routing.route_after_validate,
        {"exception_triage": "exception_triage", "match3way": "match3way"},
    )
    g.add_conditional_edges(
        "match3way",
        routing.route_after_match,
        {"exception_triage": "exception_triage", "policy": "policy"},
    )
    g.add_conditional_edges(
        "policy",
        routing.route_after_policy,
        {"exception_triage": "exception_triage", "gate": "gate"},
    )
    g.add_conditional_edges(
        "gate",
        routing.route_after_gate,
        {"auto_approve": "auto_approve", "exception_triage": "exception_triage"},
    )
    g.add_edge("auto_approve", "archive")
    g.add_edge("exception_triage", "human_review")
    g.add_edge("human_review", "archive")
    g.add_edge("archive", END)
    g.add_edge("reject", END)

    return g


def compile_graph(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[GraphState, Any]:
    """Compiled graph; checkpointer injected by the runtime (Postgres in prod)."""
    g = build_graph()
    return g.compile(checkpointer=checkpointer)

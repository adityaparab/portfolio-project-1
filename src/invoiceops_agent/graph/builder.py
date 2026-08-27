"""Graph assembly: topology per ARCHITECTURE §3.1.

ingest → extract → validate → match3way → policy → gate
    ↓(dupe)  ↓(hard err)  ↓(mismatch)  ↓(policy fail)  ↓(conf < τ)
  reject                exception_triage → human_review → archive
                                      auto_approve → archive

Two node sources over one topology:
* ``stubs`` — pure transitions, no I/O (topology/checkpoint tests, demo)
* ``PipelineNodes`` — the real wired pipeline (#25), context-injected
"""

from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from invoiceops_agent.graph import routing
from invoiceops_agent.graph.retries import RetryPolicy, retrying
from invoiceops_agent.graph.state import GraphState


class NodeSource(Protocol):
    """Anything with the eleven node callables (async, state -> partial)."""

    def ingest(self, state: GraphState) -> Any: ...

    def extract(self, state: GraphState) -> Any: ...

    def validate(self, state: GraphState) -> Any: ...

    def match3way(self, state: GraphState) -> Any: ...

    def policy(self, state: GraphState) -> Any: ...

    def gate(self, state: GraphState) -> Any: ...

    def auto_approve(self, state: GraphState) -> Any: ...

    def exception_triage(self, state: GraphState) -> Any: ...

    def human_review(self, state: GraphState) -> Any: ...

    def archive(self, state: GraphState) -> Any: ...

    def reject(self, state: GraphState) -> Any: ...


def build_graph(
    nodes: NodeSource | None = None, retry_policy: RetryPolicy | None = None
) -> StateGraph[GraphState]:
    """Assemble the topology. Real node sources get per-node INFRA-only
    retries (#27); stubs stay bare (topology tests exercise no I/O)."""
    wrap = retry_policy
    if nodes is None:  # topology-only default (tests/demo)
        from invoiceops_agent.graph.nodes import stubs

        nodes = stubs
        wrap = None

    def bound(name: str, fn: Any) -> Any:
        return retrying(name, fn, wrap) if wrap is not None else fn

    g = StateGraph(GraphState)

    g.add_node("ingest", bound("ingest", nodes.ingest))
    g.add_node("extract", bound("extract", nodes.extract))
    g.add_node("validate", bound("validate", nodes.validate))
    g.add_node("match3way", bound("match3way", nodes.match3way))
    g.add_node("policy", bound("policy", nodes.policy))
    g.add_node("gate", bound("gate", nodes.gate))
    g.add_node("auto_approve", bound("auto_approve", nodes.auto_approve))
    g.add_node("exception_triage", bound("exception_triage", nodes.exception_triage))
    g.add_node("human_review", bound("human_review", nodes.human_review))
    g.add_node("archive", bound("archive", nodes.archive))
    g.add_node("reject", bound("reject", nodes.reject))

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
    nodes: NodeSource | None = None,
    retry_policy: RetryPolicy | None = None,
) -> CompiledStateGraph[GraphState, Any]:
    """Compiled graph; checkpointer injected by the runtime (Postgres in prod)."""
    g = build_graph(nodes, retry_policy=retry_policy)
    return g.compile(checkpointer=checkpointer)

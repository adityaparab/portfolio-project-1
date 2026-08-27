---
name: langgraph-orchestration
description: Build and modify the LangGraph state machine for invoice processing (nodes, edges, checkpointing, HITL interrupts). Use whenever touching src/graph/, graph state, node logic, routing decisions, the confidence gate, retries/DLQ, or the Google ADK variant.
---

# LangGraph orchestration patterns (InvoiceOps `src/graph/`)

Read `docs/ARCHITECTURE.md` §3 before changing the graph. The state machine is the heart of the system — treat its shape as a contract.

## Topology (do not improvise)

```
Ingest → Extract → Validate → Match3Way → Policy → Gate → AutoApprove → Archive
                                              │                     ↘ ExceptionTriage → HumanReview → Archive
Ingest → Reject (duplicate hash)   ·   Validate/Match/Policy failures → ExceptionTriage
Gate → ExceptionTriage when confidence < τ   (abstention, never guess)
```

- Agent nodes: `Extract`, `ExceptionTriage` (they may call LLMs — via the gateway only). All other nodes are deterministic code.
- Checkpoint after every node (Postgres checkpointer). A node must be idempotent under replay — re-reading a checkpoint must not double-write side effects; guard writes with run-scoped idempotency.

## State discipline

- `GraphState` is a Pydantic v2 model in `src/graph/state.py`: `run_id`, `content_hash`, extraction/match/policy results, composite confidence, `route` (AUTO/EXCEPTION/REJECT), failure info. Nodes return **partial state updates**, never mutate shared dicts.
- Keep state serializable: Decimal-as-strategy per the schema module; no live handles (sessions, clients) in state — fetch them per-node via injection/config.
- Add a field only with a default that makes old checkpoints replayable; note migration in the PR.

## Routing

- Conditional edges are named functions (`route_after_gate`) with pure, unit-testable logic. The composite gate formula lives in one module (weights + τ from versioned config, not literals): `w1·min(field_conf) + w2·(1−norm_match_delta) + w3·policy_severity_term`.
- Below τ the graph MUST route to `ExceptionTriage`. Making the gate guess is the one unforgivable regression here.

## Error handling

- Infra errors (DB, storage, gateway 5xx/timeouts): retry with backoff inside the node or via LangGraph retry policies.
- Business failures (validation fail, match beyond tolerance, policy FAIL): never retry — carry the typed reason into state and route to triage.
- Exhausted retries → DLQ path with full state snapshot for admin replay.

## HITL

- `HumanReview` pauses via interrupt/checkpoint; the decision arrives through `POST /v1/exceptions/{id}/decision` and resumes the graph. The human actor, rationale, and reason_code are appended to the ledger before resume.

## Testing

- Unit-test routing functions as pure logic (table-driven: state in → route out).
- Integration: run the real graph against testcontainers Postgres with **VCR cassettes** for LLM nodes — deterministic replay, no network.
- Every topology change gets a test asserting the reachable paths.

## ADK variant (`src/graph/adk/`, Phase 6)

Port the same topology/semantic — don't redesign. Differences (checkpointing, HITL, observability) are the subject of ADR comparison; record them, don't erase them.

"""Run trace + provenance routes (issue #35)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from invoiceops_agent.api.auth import PROVENANCE_ROLES, IdentityDep
from invoiceops_agent.api.deps import get_graph_runner, get_trace_service
from invoiceops_agent.api.schemas.trace import ProvenancePackage, RunTrace
from invoiceops_agent.api.services.trace import TraceService
from invoiceops_agent.db.models import Run
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.state import GraphState
from invoiceops_agent.ledger.model_calls import reader as model_call_reader

router = APIRouter(prefix="/v1", tags=["trace"])


@router.get("/runs/{run_id}/trace", response_model=RunTrace)
async def run_trace(
    run_id: int,
    request: Request,
    identity: IdentityDep,
    service: Annotated[TraceService, Depends(get_trace_service)],
    runner: Annotated[GraphRunner | None, Depends(get_graph_runner)],
) -> RunTrace:
    """Ordered node timeline for one run — ledger events (one per node, plus
    retries/DLQ/decision extras), the checkpointed node order, the live
    active node, and the alias/wire-model per LLM stage."""

    async def node_trace_provider(invoice_id: int) -> tuple[GraphState | None, str | None]:
        if runner is None:
            return None, None
        return await runner.activity(invoice_id)

    from invoiceops_agent.graph.runtime import STAGE_PROMPT_VERSIONS

    alias_map = _alias_model_map(request)
    stage_models: dict[str, object] = {
        stage: {"alias": alias, "wire_model": alias_map.get(alias, alias)}
        for stage, alias in (
            ("extract", "extract-vision"),
            ("triage", "triage-reasoner"),
            ("policy", "embed"),  # near-dup embedding inside the policy node
        )
    }
    stage_models["prompt_versions"] = STAGE_PROMPT_VERSIONS

    trace = await service.run_trace(run_id, node_trace_provider, stage_models)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return trace


@router.get("/runs/{run_id}/model-calls")
async def run_model_calls(
    run_id: int,
    identity: IdentityDep,
    service: Annotated[TraceService, Depends(get_trace_service)],
) -> list[dict[str, object]]:
    """Recorded LLM calls for a run — reasoning + output + model + versions
    (the Agent Run step view's detail panel; append-only audit trail)."""
    async with service._sessions() as session:
        run = await session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        views = await model_call_reader.by_run(session, run_id)
    return [view.model_dump() for view in views]


def _alias_model_map(request: Request) -> dict[str, str]:
    import json as _json

    raw = request.app.state.settings.gateway_model_map_json or "{}"
    try:
        parsed = _json.loads(raw)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


@router.get("/invoices/{invoice_id}/provenance", response_model=ProvenancePackage)
async def invoice_provenance(
    invoice_id: int,
    identity: IdentityDep,
    service: Annotated[TraceService, Depends(get_trace_service)],
) -> ProvenancePackage:
    """Complete decision provenance — runs, exceptions, decisions, and the
    full ledger with version pins. Audit/platform roles only (ARCHITECTURE §5)."""
    if identity.role not in PROVENANCE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Provenance export requires role in {sorted(r.value for r in PROVENANCE_ROLES)}"
            ),
        )
    package = await service.provenance(invoice_id)
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return package

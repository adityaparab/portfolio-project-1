"""Run trace + provenance routes (issue #35)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from invoiceops_agent.api.auth import PROVENANCE_ROLES, IdentityDep
from invoiceops_agent.api.deps import get_graph_runner, get_trace_service
from invoiceops_agent.api.schemas.trace import ProvenancePackage, RunTrace
from invoiceops_agent.api.services.trace import TraceService
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.state import GraphState

router = APIRouter(prefix="/v1", tags=["trace"])


@router.get("/runs/{run_id}/trace", response_model=RunTrace)
async def run_trace(
    run_id: int,
    identity: IdentityDep,
    service: Annotated[TraceService, Depends(get_trace_service)],
    runner: Annotated[GraphRunner | None, Depends(get_graph_runner)],
) -> RunTrace:
    """Ordered node timeline for one run — ledger events (one per node, plus
    retries/DLQ/decision extras) with the checkpointed node order."""

    async def node_trace_provider(invoice_id: int) -> GraphState | None:
        if runner is None:
            return None
        return await runner.state_for(invoice_id)

    trace = await service.run_trace(run_id, node_trace_provider)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return trace


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

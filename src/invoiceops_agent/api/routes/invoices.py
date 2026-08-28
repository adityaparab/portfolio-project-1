"""Invoice routes: ingest (service token), queue + detail aggregate (RBAC)."""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from starlette.responses import JSONResponse

from invoiceops_agent.api.auth import IdentityDep, verify_service_token
from invoiceops_agent.api.deps import get_graph_runner, get_ingest_service, get_queue_service
from invoiceops_agent.api.schemas.invoices import InvoiceAccepted, InvoiceDetailAggregate, QueuePage
from invoiceops_agent.api.services.ingest import IngestService
from invoiceops_agent.api.services.queue import SORT_FIELDS, QueueService
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.state import GraphState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])

Service = Annotated[IngestService, Depends(get_ingest_service)]
Auth = Annotated[None, Depends(verify_service_token)]
Queue = Annotated[QueueService, Depends(get_queue_service)]


async def _run_pipeline(app: FastAPI, invoice_id: int) -> None:
    """Background execution of the graph for a freshly accepted invoice.

    Failures are logged, never propagated (the run stays resumable — DLQ and
    replay semantics land with #27). The runner hangs off ``app.state``
    (set by the lifespan; None means the eager build failed and uploads
    queue until a restart — visible as the ``pipeline`` readiness check).
    """
    runner: GraphRunner | None = getattr(app.state, "graph_runner", None)
    if runner is None:
        logger.warning("no graph runner — invoice %s queued unprocessed", invoice_id)
        return
    try:
        await runner.run_invoice(invoice_id)
    except Exception:
        logger.exception("pipeline failed for invoice %s (resumable)", invoice_id)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=InvoiceAccepted)
async def ingest_invoice(
    upload: UploadFile,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    _auth: Auth,
    service: Service,
) -> InvoiceAccepted:
    """Accept a raw invoice document; returns the invoice + run identity.

    Idempotency: a repeated ``Idempotency-Key`` replays the stored response
    without re-ingesting (the middleware attaches the key to request.state).
    A fresh (non-duplicate) acceptance schedules the processing graph as a
    background task — the run state is observable via the ledger/trace.
    """
    idem_key = getattr(request.state, "idempotency_key", None)
    store = request.app.state.idempotency_store
    if idem_key is not None:
        cached = await store.get(idem_key)
        if cached is not None and cached.body:
            return InvoiceAccepted.model_validate_json(cached.body)

    result = await service.ingest(upload)
    accepted = result.accepted
    response.status_code = status.HTTP_200_OK if result.duplicate else status.HTTP_201_CREATED

    if not result.duplicate:
        background.add_task(_run_pipeline, request.app, accepted.invoice_id)

    if idem_key is not None:
        replayable = JSONResponse(content=accepted.model_dump(), status_code=201)
        await store.put(idem_key, replayable)

    return accepted


@router.get("", response_model=QueuePage)
async def list_invoices(
    identity: IdentityDep,
    queue: Queue,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    route: str | None = None,
    exception_type: Annotated[str | None, Query(alias="exception_type")] = None,
    severity: str | None = None,
    assignee: str | None = None,
    vendor_id: int | None = None,
    sort: Annotated[str, Query()] = "created_at",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QueuePage:
    """Filterable, server-side-paginated queue (Maria's list view, #28)."""
    if sort not in SORT_FIELDS:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"sort must be one of {list(SORT_FIELDS)}",
        )
    return await queue.list_invoices(
        status=status_filter,
        route=route,
        exception_type=exception_type,
        severity=severity,
        assignee=assignee,
        vendor_id=vendor_id,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get("/{invoice_id}", response_model=InvoiceDetailAggregate)
async def invoice_detail(
    invoice_id: int,
    identity: IdentityDep,
    queue: Queue,
    runner: Annotated[GraphRunner | None, Depends(get_graph_runner)],
) -> InvoiceDetailAggregate:
    """Full aggregate — invoice + extraction (confidences) + match deltas +
    policy findings + exception/triage + ledger summary (RBAC-filtered)."""
    from fastapi import HTTPException

    aggregate = await queue.detail(invoice_id, identity, _state_provider(runner))
    if aggregate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return aggregate


def _state_provider(
    runner: GraphRunner | None,
) -> Callable[[int], Awaitable[GraphState | None]]:
    async def provide(invoice_id: int) -> GraphState | None:
        if runner is None:
            return None
        return await runner.state_for(invoice_id)

    return provide

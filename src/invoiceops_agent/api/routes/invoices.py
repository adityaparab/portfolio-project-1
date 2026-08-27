"""POST /v1/invoices — authenticated multipart upload, idempotent."""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, UploadFile, status
from starlette.responses import JSONResponse

from invoiceops_agent.api.auth import verify_service_token
from invoiceops_agent.api.deps import get_ingest_service
from invoiceops_agent.api.schemas.invoices import InvoiceAccepted
from invoiceops_agent.api.services.ingest import IngestService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])

Service = Annotated[IngestService, Depends(get_ingest_service)]
Auth = Annotated[None, Depends(verify_service_token)]


async def _run_pipeline(app: object, invoice_id: int) -> None:
    """Background execution of the graph for a freshly accepted invoice.

    Failures are logged, never propagated (the run stays resumable — DLQ and
    replay semantics land with #27).
    """
    runner = getattr(app, "graph_runner", None)
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

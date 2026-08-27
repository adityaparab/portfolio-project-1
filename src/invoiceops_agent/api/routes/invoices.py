"""POST /v1/invoices — authenticated multipart upload, idempotent."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, UploadFile, status
from starlette.responses import JSONResponse

from invoiceops_agent.api.auth import verify_service_token
from invoiceops_agent.api.deps import get_ingest_service
from invoiceops_agent.api.schemas.invoices import InvoiceAccepted
from invoiceops_agent.api.services.ingest import IngestService

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])

Service = Annotated[IngestService, Depends(get_ingest_service)]
Auth = Annotated[None, Depends(verify_service_token)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=InvoiceAccepted)
async def ingest_invoice(
    upload: UploadFile,
    request: Request,
    response: Response,
    _auth: Auth,
    service: Service,
) -> InvoiceAccepted:
    """Accept a raw invoice document; returns the invoice + run identity.

    Idempotency: a repeated ``Idempotency-Key`` replays the stored response
    without re-ingesting (the middleware attaches the key to request.state).
    """
    idem_key = getattr(request.state, "idempotency_key", None)
    store = request.app.state.idempotency_store
    if idem_key is not None:
        cached = await store.get(idem_key)
        if cached is not None and cached.body:
            return InvoiceAccepted.model_validate_json(cached.body)

    result = await service.ingest(upload)
    accepted = result.accepted

    if idem_key is not None:
        replayable = JSONResponse(content=accepted.model_dump(), status_code=201)
        await store.put(idem_key, replayable)

    return accepted

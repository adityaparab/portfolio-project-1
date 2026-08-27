"""POST /v1/invoices/email-webhook — HMAC-verified email ingestion (issue #12).

Contract (stub email source):
  headers: X-Signature-Timestamp (unix seconds), X-Signature (hex HMAC-SHA256
           of "{timestamp}.{raw_body}" with INVOICEOPS_EMAIL_WEBHOOK_SECRET)
  body:    EmailWebhookPayload JSON

Verification order: signature (constant-time) → timestamp freshness window →
message_id replay (idempotent). Verified payloads reuse the exact ingest
pipeline (MinIO + rows + ledger + duplicate semantics from #13).
"""

import hashlib
import hmac
import io
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.datastructures import UploadFile
from starlette.datastructures import Headers

from invoiceops_agent.api.auth import verify_service_token
from invoiceops_agent.api.deps import get_ingest_service
from invoiceops_agent.api.schemas.invoices import InvoiceAccepted
from invoiceops_agent.api.schemas.webhook import EmailWebhookPayload
from invoiceops_agent.api.services.ingest import IngestService
from invoiceops_agent.api.settings import Settings

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])

Service = Annotated[IngestService, Depends(get_ingest_service)]
Auth = Annotated[None, Depends(verify_service_token)]

DEFAULT_FRESHNESS_SECONDS = 300


def _verify_signature(secret: str, timestamp: str, raw_body: bytes, signature: str) -> None:
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature.")


def _verify_freshness(timestamp: str, now: float, window_seconds: int) -> None:
    try:
        ts = float(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed signature timestamp."
        ) from exc
    if abs(now - ts) > window_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signature timestamp outside freshness window.",
        )


@router.post("/email-webhook", status_code=status.HTTP_201_CREATED, response_model=InvoiceAccepted)
async def email_webhook(
    request: Request,
    response: Response,
    payload: EmailWebhookPayload,
    _auth: Auth,
    service: Service,
) -> Any:
    settings: Settings = request.app.state.settings
    raw_body = await request.body()
    timestamp = request.headers.get("X-Signature-Timestamp", "")
    signature = request.headers.get("X-Signature", "")

    _verify_signature(settings.email_webhook_secret, timestamp, raw_body, signature)
    _verify_freshness(timestamp, time.time(), settings.email_webhook_freshness_seconds)

    # Replay protection: same message_id returns the original outcome without
    # re-ingesting (shared idempotency store, namespaced).
    store = request.app.state.idempotency_store
    replay_key = f"email:{payload.message_id}"
    cached = await store.get(replay_key)
    if cached is not None and cached.body:
        response.status_code = status.HTTP_200_OK
        return InvoiceAccepted.model_validate_json(cached.body)

    upload = UploadFile(
        file=io.BytesIO(payload.attachment.decode()),
        filename=payload.attachment.filename,
        headers=Headers({"content-type": payload.attachment.content_type}),
    )
    result = await service.ingest(upload)
    accepted = result.accepted
    response.status_code = status.HTTP_200_OK if result.duplicate else status.HTTP_201_CREATED

    from starlette.responses import JSONResponse

    await store.put(replay_key, JSONResponse(content=accepted.model_dump(), status_code=201))
    return accepted

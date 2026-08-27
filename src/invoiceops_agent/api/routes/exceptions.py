"""Exception routes: the human decision write path (#29)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from invoiceops_agent.api.auth import IdentityDep
from invoiceops_agent.api.deps import get_decision_service
from invoiceops_agent.api.schemas.decisions import DecisionRequest, DecisionResponse
from invoiceops_agent.api.services.decisions import DecisionConflict, DecisionService

router = APIRouter(prefix="/v1/exceptions", tags=["exceptions"])


@router.post(
    "/{exception_id}/decision",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def decide_exception(
    exception_id: int,
    request: DecisionRequest,
    response: Response,
    identity: IdentityDep,
    service: Annotated[DecisionService, Depends(get_decision_service)],
) -> DecisionResponse | JSONResponse:
    """Record a human decision on an exception.

    Four-eyes: APPROVE by the exception's assignee is rejected (409). A
    resolved exception replays the identical decision idempotently (200,
    ``X-Idempotent-Replay``) and rejects a conflicting one (409). Resuming
    actions continue the paused graph through HumanReview to Archive.
    """
    try:
        result = await service.decide(exception_id, request, identity)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found"
        ) from None
    except DecisionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail.model_dump(),
        ) from exc
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Idempotent-Replay"] = "true"
    return result

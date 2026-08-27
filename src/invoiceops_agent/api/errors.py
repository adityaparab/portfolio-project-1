"""RFC 7807 problem+json error handling for the whole API."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem(
    status_code: int,
    title: str,
    detail: str,
    instance: str,
    **extensions: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://invoiceops.dev/problems/{status_code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
    }
    body.update(extensions)
    return JSONResponse(status_code=status_code, content=body, media_type=_PROBLEM_MEDIA_TYPE)


def register_error_handlers(app: FastAPI) -> None:
    """Attach uniform RFC 7807 handlers to the application."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Structured details (e.g. duplicate-content payloads) ride along as an
        # `extra` extension so problem+json stays spec-shaped.
        if isinstance(exc.detail, dict):
            return _problem(
                exc.status_code,
                title=exc.detail.get("message", "HTTP error"),
                detail=str(exc.detail.get("message", "")),
                instance=str(request.url.path),
                extra=exc.detail,
            )
        return _problem(
            exc.status_code,
            title=str(exc.detail) if exc.detail else "HTTP error",
            detail=str(exc.detail),
            instance=str(request.url.path),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _problem(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation error",
            detail="One or more request fields failed validation.",
            instance=str(request.url.path),
            # ctx/input can carry exception objects or raw bytes; keep the
            # serializable subset only
            errors=[
                {"type": err.get("type"), "loc": list(err.get("loc", ())), "msg": err.get("msg")}
                for err in exc.errors()
            ],
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Server errors must not leak internals; full detail goes to logs with context.
        run_id = getattr(request.state, "run_id", None)
        logger.exception(
            "unhandled exception",
            extra={"path": request.url.path, "run_id": run_id, "exc": repr(exc)},
        )
        return _problem(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal server error",
            detail="An unexpected error occurred. See server logs (run_id context attached).",
            instance=str(request.url.path),
        )

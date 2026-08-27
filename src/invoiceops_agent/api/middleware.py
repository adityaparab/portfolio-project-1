"""Idempotency-key middleware with a pluggable response store.

The middleware only parses and attaches the key (and rejects malformed keys);
storing/replaying responses is the mutating endpoints' job (issue #11) via the
``IdempotencyStore`` on ``app.state``.
"""

from typing import Protocol

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_HEADER = "Idempotency-Key"
_MAX_KEY_LENGTH = 128


class IdempotencyStore(Protocol):
    """Storage seam for idempotent mutation responses (impl: #11)."""

    async def get(self, key: str) -> Response | None: ...

    async def put(self, key: str, response: Response) -> None: ...


class InMemoryIdempotencyStore:
    """Default store: per-process only, replaced by a persistent impl in #11."""

    def __init__(self) -> None:
        self._entries: dict[str, Response] = {}

    async def get(self, key: str) -> Response | None:
        return self._entries.get(key)

    async def put(self, key: str, response: Response) -> None:
        self._entries[key] = response


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Parse ``Idempotency-Key`` and expose it on ``request.state``."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        key = request.headers.get(_HEADER)
        if key is not None:
            key = key.strip()
            if not key or len(key) > _MAX_KEY_LENGTH:
                return Response(
                    status_code=400,
                    media_type="application/problem+json",
                    content=(
                        '{"title": "Invalid Idempotency-Key", '
                        '"detail": "Header must be 1-128 non-space characters.", '
                        '"status": 400}'
                    ),
                )
            request.state.idempotency_key = key
        return await call_next(request)

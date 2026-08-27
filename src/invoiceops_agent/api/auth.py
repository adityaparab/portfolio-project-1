"""Service-token authentication for machine-to-machine endpoints."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from invoiceops_agent.api.settings import Settings

_bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def verify_service_token(request: Request, credentials: Credentials) -> None:
    """Reject requests without a valid service bearer token (401 problem+json)."""
    settings: Settings = request.app.state.settings
    if credentials is None or credentials.credentials != settings.service_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid service token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

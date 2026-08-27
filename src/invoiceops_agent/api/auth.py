"""Authentication: service tokens (machine) + persona identity (users, demo stub).

Personas mirror docs/USER_JOURNEY.md: Maria (analyst), Dan (manager), Priya
(audit), Platform Eng (platform). The React persona switcher sends these
headers; the API trusts them in the demo (ARCHITECTURE §5 "Auth (demo:
stubbed)") — production swaps the same dependency for real JWT claims.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from invoiceops_agent.api.settings import Settings

_bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]

USER_HEADER = "X-IO-User"
ROLE_HEADER = "X-IO-Role"


class Role(StrEnum):
    ANALYST = "analyst"  # Maria — works the exception queue
    MANAGER = "manager"  # Dan — dashboards, approvals above limits
    AUDIT = "audit"  # Priya — provenance, version pins, full ledger
    PLATFORM = "platform"  # ops — everything


PROVENANCE_ROLES = frozenset({Role.AUDIT, Role.PLATFORM})


@dataclass(frozen=True)
class Identity:
    user: str
    role: Role


def get_identity(request: Request) -> Identity:
    """Persona identity from headers (demo stub — see module docstring)."""
    raw_user = request.headers.get(USER_HEADER, "demo@invoiceops")
    raw_role = request.headers.get(ROLE_HEADER, Role.ANALYST.value)
    try:
        role = Role(raw_role.lower().strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unknown role {raw_role!r}; known: {[r.value for r in Role]}",
        ) from None
    return Identity(user=raw_user.strip() or "demo@invoiceops", role=role)


IdentityDep = Annotated[Identity, Depends(get_identity)]


def verify_service_token(request: Request, credentials: Credentials) -> None:
    """Reject requests without a valid service bearer token (401 problem+json)."""
    settings: Settings = request.app.state.settings
    if credentials is None or credentials.credentials != settings.service_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid service token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

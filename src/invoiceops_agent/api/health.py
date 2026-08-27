"""Liveness and dependency-aware readiness endpoints."""

import logging
from typing import Any
from urllib.parse import urljoin

import asyncpg
import httpx
from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from invoiceops_agent.api.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()


class DependencyCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class Readiness(BaseModel):
    status: str = Field(pattern="^(ok|degraded)$")
    checks: list[DependencyCheck]


async def check_postgres(dsn: str) -> DependencyCheck:
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=2.0)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
    except Exception as exc:  # readiness must never raise
        return DependencyCheck(name="postgres", ok=False, detail=repr(exc))
    return DependencyCheck(name="postgres", ok=True)


async def check_http(
    name: str, base_url: str, path: str, headers: dict[str, str]
) -> DependencyCheck:
    url = urljoin(base_url if base_url.endswith("/") else base_url + "/", path.lstrip("/"))
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code < 400:
            return DependencyCheck(name=name, ok=True)
        return DependencyCheck(name=name, ok=False, detail=f"HTTP {resp.status_code} from {url}")
    except Exception as exc:
        return DependencyCheck(name=name, ok=False, detail=repr(exc))


async def _readiness(settings: Settings) -> Readiness:
    checks = [
        await check_postgres(settings.database_dsn),
        await check_http("minio", settings.minio_base_url, "/minio/health/live", {}),
        await check_http(
            "litellm",
            settings.litellm_base_url,
            "/health/liveliness",
            {"Authorization": f"Bearer {settings.litellm_api_key}"},
        ),
    ]
    degraded = any(not c.ok for c in checks)
    return Readiness(status="degraded" if degraded else "ok", checks=checks)


@router.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, Any]:
    """Liveness: process is up. No dependency calls."""
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"])
async def readyz(request: Request) -> Any:
    """Readiness: dependencies reachable. 503 (with body) when degraded."""
    readiness = await _readiness(request.app.state.settings)
    if readiness.status != "ok":
        logger.warning("readiness degraded: %s", readiness.model_dump())
        return _degraded_response(readiness)
    return readiness


def _degraded_response(readiness: Readiness) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=readiness.model_dump(),
        media_type="application/problem+json",
    )

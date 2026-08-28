"""Metrics summary route for the Dashboard (issue #33)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from invoiceops_agent.api.deps import get_dashboard_service
from invoiceops_agent.api.services.dashboard import DashboardService, DashboardSummary

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("/summary", response_model=DashboardSummary)
async def metrics_summary(
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> DashboardSummary:
    """Dashboard aggregates, computed server-side (display-only upstream)."""
    return await service.summary(days)

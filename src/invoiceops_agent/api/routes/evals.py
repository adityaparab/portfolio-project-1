"""Eval reports API (issue #38): the Evals screen reads versioned reports
from ``eval/reports/`` (written by the Phase-5 harness, #45-#51).

Report contract (documented for #47): each ``eval/reports/<name>.json`` is::

    {"report_version": "...", "generated_at": "...", "model_class": "...",
     "dataset_version": "...", "metrics": {"<name>": {"value": n, "target": n}},
     "confusion": [{"code": "PRICE_MM", "tp": 1, "fp": 0, "fn": 0}],
     "tau_sweep": [{"tau": 0.8, "stp_rate": 0.7, "missed_anomaly_rate": 0.02}]}

``index.json`` lists report filenames; absence yields an empty list — the
harness is not built yet (Phase 5), and the screen says so.
"""

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/evals", tags=["evals"])

REPORTS_DIR = Path(__file__).resolve().parents[3] / "eval" / "reports"


class EvalReportEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str


class EvalReportIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    reports: list[EvalReportEntry] = Field(default_factory=list)


def _load_index() -> list[str]:
    index_path = REPORTS_DIR / "index.json"
    if not index_path.exists():
        return []
    try:
        payload = json.loads(index_path.read_text())
        return [str(name) for name in payload.get("reports", [])]
    except (OSError, ValueError):
        logger.warning("eval reports index unreadable", exc_info=True)
        return []


@router.get("/reports", response_model=EvalReportIndex)
async def list_eval_reports() -> EvalReportIndex:
    """Versioned eval reports (experiment log). Empty until Phase 5 runs."""
    return EvalReportIndex(reports=[EvalReportEntry(name=name) for name in _load_index()])


@router.get("/reports/{name}")
async def eval_report(name: str) -> dict[str, Any]:
    """One report by filename (path-safe names only)."""
    if "/" in name or ".." in name or not name.endswith(".json"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bad name")
    path = REPORTS_DIR / name
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    try:
        payload: dict[str, Any] = json.loads(path.read_text())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="report unreadable"
        ) from None
    return payload

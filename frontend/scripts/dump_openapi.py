"""Dump the FastAPI OpenAPI schema without running a server (#31 codegen).

Run from the repo root via uv so the backend package resolves::

    uv run python frontend/scripts/dump_openapi.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from invoiceops_agent.api.main import app  # noqa: E402

OUT = Path(__file__).resolve().parent / "openapi.json"
OUT.write_text(json.dumps(app.openapi(), indent=2))
print(OUT)

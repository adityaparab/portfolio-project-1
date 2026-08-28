# InvoiceOps — single-image deploy: API + built SPA served on :8000.

# UI build (issue: single-command deploy). VITE_API_BASE_URL="" bakes
# same-origin calls (/v1/*) — the runtime api serves this SPA at /.
FROM node:24-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
ENV VITE_API_BASE_URL=""
RUN npm run build

FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.5.9 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching; then the package itself.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY eval ./eval
COPY alembic.ini README.md ./
RUN uv sync --locked --no-dev

COPY --from=ui /ui/dist ./ui_dist
ENV INVOICEOPS_UI_DIST=/app/ui_dist

RUN useradd --create-home --uid 10001 invoiceops
USER invoiceops

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

# --no-sync: the venv is fully baked (and root-owned) from the build above;
# runtime must never try to mutate it as the non-root user.
CMD ["uv", "run", "--no-sync", "--no-dev", "uvicorn", "invoiceops_agent.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]

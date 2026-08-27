---
name: devstack-ops
description: Run and change the InvoiceOps local stack and CI — Docker Compose services (api, worker, postgres, minio, litellm, langfuse, grafana, prometheus, seed, ollama-backed models), GitHub Actions pipeline, uv dependency management. Use whenever starting/stopping services, adding dependencies, debugging Compose, or editing CI workflows.
---

# Dev stack & CI patterns (Compose + uv + GitHub Actions)

## Commands

```bash
uv sync                                  # install/refresh env from lock
uv run pytest                            # run tests (unit subset locally)
uv add <pkg>                             # add a dependency (backend)
docker compose up -d                     # start stack (from deploy/ or root compose)
docker compose logs -f api               # follow one service
docker compose run --rm seed             # (re)seed synthetic ERP data
```

- **All dependency changes go through `uv`** — `uv add`/`uv remove` (updates `pyproject.toml` + `uv.lock`). Never edit `pyproject.toml` dependencies by hand without re-locking; never pip-install.
- Frontend deps: `npm install <pkg>` in `frontend/` with the lockfile committed.

## Compose topology (root `compose.yaml`, see `deploy/`)

`api` · `worker` · `postgres` (pgvector image) · `minio` · `litellm` (`deploy/litellm/config.yaml` holds the alias→model routing) · `langfuse` · `grafana` + `prometheus` · one-shot `seed`. Dev LLM backends: Ollama reached **through LiteLLM only**.

Conventions:
- Service configs from env with sane local defaults in a committed `.env.example`; real secrets never committed.
- Volumes for postgres/minio data; everything else stateless. Healthchecks on `/healthz`/`/readyz` where applicable; `depends_on` with `condition: service_healthy`.
- Adding a service: healthcheck + env example + dashboard/prometheus wiring if it exposes metrics.

## CI (`.github/workflows/ci.yml`)

Stage order is contractual: ruff → mypy --strict → unit → integration (testcontainers) → eval gate → build. Changes to stage order or skip logic need an explicit rationale in the PR. Frontend lint/typecheck/test in parallel tracks; Playwright smoke after the stack builds.

## Debugging the stack

1. `docker compose ps` — what's unhealthy; check healthcheck logs first.
2. Gateway/model errors → inspect `litellm` service logs and `deploy/litellm/config.yaml` alias mapping before touching app code (mirrors `llm-gateway-litellm` skill).
3. DB state suspicion → `docker compose exec postgres psql -U ...`; remember `ledger`/`decisions` reject UPDATE/DELETE by design.
4. Port conflicts/changed ports: fix in `.env` (committed defaults unchanged), not by editing service files locally.

## Hygiene

- Compose files are production-shaped but local-first: profiles for heavy services (`--profile obs`) are fine; document any profile in the README section of `IMPLEMENTATION_PLAN.md`.
- Image pinning: service images tagged (no `latest`), base images for our Dockerfiles pinned + updated deliberately.

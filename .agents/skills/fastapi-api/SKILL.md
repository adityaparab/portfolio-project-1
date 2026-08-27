---
name: fastapi-api
description: Build FastAPI + Pydantic v2 async services for this repo's src/api layer. Use whenever adding or changing API endpoints, request/response models, middleware, dependencies, error handling, or OpenAPI concerns in the InvoiceOps backend — even for a small endpoint tweak.
---

# FastAPI + Pydantic v2 service patterns (InvoiceOps `src/api/`)

Read `AGENTS.md` first if you haven't. Read `docs/ARCHITECTURE.md` §5 for the endpoint catalogue before adding routes.

## Hard rules

- Endpoints are **thin**: parse → call a service/graph function → serialize. Business logic lives in `src/graph/`, `src/tools/`, `src/agents/` — never in a route handler.
- All request/response bodies are Pydantic v2 models in a schema module per resource (`src/api/schemas/`). Zod twins in `frontend/` must be updated in the same commit when these change.
- Errors return RFC 7807 `application/problem+json` via the app-wide exception handlers. Raise the typed internal exceptions; do not hand-build error JSON in handlers.
- Every externally-triggered mutation accepts an `Idempotency-Key` header; duplicate keys return the original result.
- Async by default. No blocking I/O in handlers (no `requests`, no sync DB sessions, no `time.sleep`).

## Canonical endpoint shape

```python
@router.post("/v1/invoices", status_code=201, response_model=InvoiceAccepted)
async def ingest_invoice(
    upload: UploadFile,
    service: IngestService = Depends(get_ingest_service),
) -> InvoiceAccepted:
    doc = await service.ingest(upload=upload)   # service owns MinIO + hash dedupe
    return InvoiceAccepted.model_validate(doc)
```

- Dependencies (`Depends`) provide DB session, settings, gateway client — tests override them via `app.dependency_overrides`. If a handler constructs its own dependencies, refactor.
- Settings come from `pydantic-settings` (`get_settings()` cached dependency), never `os.getenv` inline.

## Pydantic v2 idioms

- `model_config = ConfigDict(frozen=True)` for value objects; `model_validate` over `parse_obj` (v1 names are bugs here).
- Money/quantity fields are `Decimal` with explicit serialization config; dates are tz-aware UTC.
- Response models double as the OpenAPI contract consumed by `frontend/` codegen — breaking a field is a breaking API change; version or coordinate.

## Testing

- Unit tests use `httpx.AsyncClient` + `ASGITransport` with dependency overrides; no live server, no network.
- Schemathesis runs against the OpenAPI schema in CI — keep response models truthful.

## Common mistakes to avoid

- Writing match/policy logic inside a route (belongs in `src/tools/`, pure and deterministic).
- New endpoint without a ledger entry for the action it performs — check `src/ledger/` integration is included.
- Returning ORM objects directly instead of response models (leaks schema, breaks Zod mirrors).

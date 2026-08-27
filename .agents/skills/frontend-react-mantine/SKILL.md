---
name: frontend-react-mantine
description: Frontend development for the InvoiceOps UI — React + TypeScript + Vite + Mantine, TanStack Query/Table, react-hook-form + Zod, Recharts, React Router. Use whenever building or changing screens, components, API client calls, forms, tables, or charts in frontend/.
---

# Frontend patterns (React + TS + Vite + Mantine)

Screens of record: the six in `IMPLEMENTATION_PLAN.md` Phase 3 (Dashboard, Intake, Agent Run, Exception Review, Audit/Trace, Evals) — behavior references are `mocks/index.html` and `docs/USER_JOURNEY.md`.

## Project conventions

- `frontend/` Vite app, TypeScript `strict`. `npm run` scripts: `dev`, `build`, `test` (Vitest), `lint`/`typecheck`. Dev server proxies `/api` to the FastAPI service per Vite config.
- **API layer:** typed client generated from FastAPI's OpenAPI schema (`openapi-fetch` or codegen script). Responses parsed through **Zod** schemas that mirror the Pydantic contracts — when a backend model changes, update its Zod twin in the same commit.
- **Server state = TanStack Query only.** No `useEffect`+fetch. Query keys are hierarchical arrays (`['invoices', filter]`); mutations invalidate exactly the keys they touch (a decision mutation invalidates that exception + queue lists + dashboard aggregates).
- **Routing:** React Router with a persona switcher (Maria/Dan/Priya/Platform) driving RBAC-gated routes; the switcher sets the same identity headers the API RBAC checks.

## Component rules

- UI primitives come from **Mantine** (`@mantine/core`, `@mantine/hooks`, `@mantine/notifications`, `@mantine/dates`): never hand-roll modals, toasts, selects, date pickers. App theme set once in the provider — no ad-hoc hex values in components.
- Feature logic lives in hooks (`useExceptionQueue`, `useDecision`); components stay presentational and small. Props fully typed; `any` only with a justification comment (target: zero).
- Tables (exception queue, invoices): **TanStack Table** with Mantine styling; server-side pagination/sort/filter wired to query params — no client-side illusion of server data.
- Charts (Dashboard, Evals): **Recharts**; metrics come from the API (`/v1/metrics` aggregates / eval reports), never recomputed from raw lists client-side.
- Money/timestamps formatted through shared `lib/format` helpers (Decimal-safe strings from API; tz-aware display).

## Forms (decision flows etc.)

- **react-hook-form + Zod resolver.** The Zod schema doubles as the source for validation rules; server-side RFC 7807 errors map onto form fields by key. The decision form enforces rationale + reason_code presence, and the four-eyes flow (approver ≠ submitter) surfaces as a distinct confirmation step, mirroring API checks.

## Live views

- Agent Run screen polls (or subscribes via SSE when added) the run trace endpoint at a single interval hook; all derived rendering is pure functions of the trace state.

## Testing

- Vitest + React Testing Library for forms, queue behavior, RBAC gating. Mock the API layer at the generated-client boundary with cassette-like fixtures — components test against realistic payloads.
- One Playwright happy-path smoke (ingest → exception → decision) runs in CI against the Compose stack.

## Avoid

- Duplicating backend business logic (match tolerance, policy) in TS — display what the API decided.
- Inline fetching, inline styles over the Mantine theme, untyped API payloads (`as Foo` casts at the boundary instead of Zod parsing).

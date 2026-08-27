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

- UI primitives come from **Mantine** (`@mantine/core`, `@mantine/hooks`, `@mantine/notifications`, `@mantine/dates`): never hand-roll modals, toasts, selects, date pickers. Components are used for structure and behavior only — visual styling never passes through their style props (see Styling).
- Feature logic lives in hooks (`useExceptionQueue`, `useDecision`); components stay presentational and small. Props fully typed; `any` only with a justification comment (target: zero).
- Tables (exception queue, invoices): **TanStack Table** with CSS-Module-styled markup; server-side pagination/sort/filter wired to query params — no client-side illusion of server data.
- Charts (Dashboard, Evals): **Recharts**; metrics come from the API (`/v1/metrics` aggregates / eval reports), never recomputed from raw lists client-side.
- Money/timestamps formatted through shared `lib/format` helpers (Decimal-safe strings from API; tz-aware display).

## Styling — CSS Modules, always

Styling code never lives in `.ts`/`.tsx` files. If it draws, it lives in a CSS Module.

- **One module per component, colocated, same base name**: `ExceptionTable.tsx` ↔ `ExceptionTable.module.css`. Import as `import styles from "./ExceptionTable.module.css"` and apply via `className={styles.row}`. Vite compiles `.module.css` out of the box; keys are `camelCase` so `styles.someClass` stays typed.
- **No inline `style={{ ... }}` props.** The single exception is passing **data-derived values as CSS custom properties** — `style={{ "--bar-fill": `${pct}%` }}` — with every actual style rule in the module: `<div className={styles.fillBar} style={{ "--bar-fill": fill }}>` and `.fillBar { width: var(--bar-fill); }`. Static values never take this path.
- **No Mantine visual style props**: `mt=`, `mb=`, `p=`, `m=`, `c=`, `bg=`, `fz=`, `fw=`, `w=`, `h=`, `miw=`, `opacity=`, `radius=` etc. are styling in disguise and are forbidden in tsx. Non-visual API props (`opened`, `data`, `label`, `placeholder`, `disabled`, `position` for drawer anchor, `variant`/`size` selecting a themed variant token) are fine. Spacing around a Mantine component goes on the wrapper's module class or the component's own module class.
- Mantine's own component classes are targeted **from the module** via `:global()` selectors when internals need nudging (e.g. `.row :global(.mantine-Table-td) { … }`) — never by patching `theme.components` ad hoc per screen.
- **Global styles exist once**: `src/styles/global.css` (reset, CSS custom properties/design tokens) imported only in `main.tsx`, plus the Mantine theme token object in `AppProvider.tsx`. Everything else is scoped to a module. Shared cross-component styles become a shared module (`styles/shared.module.css`) imported where needed — never global class names.
- Design tokens (colors, spacing, radii, font sizes) are CSS custom properties in `global.css` (`var(--io-space-2)`); modules reference tokens instead of raw values. The Mantine theme reads the same tokens so both systems stay in sync.
- The frontend lint setup (added with the 3.4 scaffold) enforces this: `react/forbid-component-props` banning the style props above, and a `no-restricted-syntax` guard on object-literal `style` attributes (custom-property spreads excepted). Keep those rules green — don't disable them per-line.

## Forms (decision flows etc.)

- **react-hook-form + Zod resolver.** The Zod schema doubles as the source for validation rules; server-side RFC 7807 errors map onto form fields by key. The decision form enforces rationale + reason_code presence, and the four-eyes flow (approver ≠ submitter) surfaces as a distinct confirmation step, mirroring API checks.

## Live views

- Agent Run screen polls (or subscribes via SSE when added) the run trace endpoint at a single interval hook; all derived rendering is pure functions of the trace state.

## Testing

- Vitest + React Testing Library for forms, queue behavior, RBAC gating. Mock the API layer at the generated-client boundary with cassette-like fixtures — components test against realistic payloads.
- One Playwright happy-path smoke (ingest → exception → decision) runs in CI against the Compose stack.

## Avoid

- Duplicating backend business logic (match tolerance, policy) in TS — display what the API decided.
- Inline fetching; untyped API payloads (`as Foo` casts at the boundary instead of Zod parsing).
- Styling inside component code in any form: inline `style` objects (except data-driven CSS custom properties), Mantine style props (`mt=`, `c=`, `bg=`, …), global class names, or raw hex/spacing literals in tsx. When a screen "just needs a tweak," the tweak goes into its `.module.css`.

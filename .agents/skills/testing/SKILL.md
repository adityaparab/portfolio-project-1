---
name: testing
description: Test strategy and mechanics for InvoiceOps — pytest unit/integration split, VCR cassettes for LLM calls, testcontainers, hypothesis, schemathesis, Vitest/RTL, Playwright smoke, and the CI eval gate. Use whenever writing or running tests, fixing flaky tests, or changing CI test stages.
---

# Testing patterns (InvoiceOps)

`docs/ARCHITECTURE.md` §9 and `docs/EVALUATION.md` define the strategy; this is the operational version.

## The pyramid (and where each kind runs)

1. **Unit** (local + CI, fast): pure logic — routing functions, match tolerance math, policy rules, Zod schemas, form logic. Everything at the edges mocked/injected.
2. **Integration** (CI): real Postgres/MinIO via **testcontainers**, real graph runs with **VCR cassettes** for every LLM call, API via `httpx.AsyncClient` + `ASGITransport`.
3. **Eval** (CI, Phase 5+): the golden dataset through the real Compose stack — never in pre-commit. **Schemathesis** fuzzes the OpenAPI contract.

## Determinism is the whole game

- **Cassettes, not live calls:** record each LLM interaction once (per alias + scenario) through the gateway wrapper; replay offline. New prompt version ⇒ new cassette scenario; don't edit old ones to "make it pass".
- Seeds pinned everywhere: `faker.seed(...)`, `random.seed(...)`, dataset generator versions; time via injected/frozen clock (`freezegun` or clock port) when logic reads it.
- No ordering coupling: tests never depend on execution order or shared mutable fixtures beyond their scope. A test that passes alone but fails in the suite is a bug in the test.

## Authoring rules

- Failing test first for bug fixes; tests ship in the same PR as the code (AGENTS.md definition of done).
- Factories over fixtures-inheritance for entities; each test builds the minimal world it needs.
- Use **hypothesis** for invariant-heavy pure logic (tax/math validation, gate formula monotonicity, tolerance bands).
- Name tests after behavior (`test_gate_routes_below_tau_to_triage`), not after methods.
- Frontend: Vitest + RTL with the API mocked at the generated-client boundary; Playwright smoke covers ingest → exception → decision against Compose.

## CI wiring (`.github/workflows/ci.yml`)

Order: ruff → mypy --strict → unit → integration (testcontainers) → **eval gate** → build. The eval gate (`eval/ci_gate.py`) fails the PR on any primary metric regressing >0.5% absolute vs main or below floor. **Never weaken a threshold or skip a stage to get green** — investigate or revert; if a metric definition itself is wrong, that's a reviewed change with an experiment-log entry, not a quiet edit.

## Flakiness protocol

1. Reproduce locally with the same seeds/cassettes; if it can't reproduce, suspect ordering, shared state, or wall-clock.
2. Fix the root cause (usually a missing injection seam — see AGENTS.md testability rules).
3. Never `@pytest.mark.flaky`-style retries to bury it.

---
name: data-layer
description: PostgreSQL + SQLAlchemy 2 + Alembic + pgvector data-layer work for this repo — models, migrations, the append-only ledger, ERP simulation tables, MinIO document storage. Use whenever creating or changing database tables, writing repositories/queries, or storing/fetching raw documents.
---

# Data layer patterns (InvoiceOps Postgres/MinIO)

Schema of record: `docs/ARCHITECTURE.md` §6. Migration tool: Alembic (never `create_all` outside throwaway scratch scripts).

## Tables & conventions

- Core: `vendors`, `purchase_orders` (lines JSONB), `goods_receipts`, `invoices` (`content_hash` UNIQUE), `invoice_lines`, `runs`, `checkpoints` (state JSONB), `ledger`, `exceptions`, `decisions`.
- SQLAlchemy 2.0 style: `DeclarativeBase`, `Mapped[...]` / `mapped_column(...)`, typed. Repos live in `src/tools/` (ERP repo) and `src/ledger/`; sessions injected, never global.
- Money/quantities `Numeric`, timestamps `timestamptz` UTC.
- pgvector column on invoices/embeddings for near-duplicate detection; plain btree where queries don't need it.

## Append-only enforcement (non-negotiable)

`ledger` and `decisions`:
- No UPDATE/DELETE methods on their repositories — do not write them "for convenience."
- Enforced at DB level by grants + trigger (Phase 0 migrations); schema changes here get extra review.
- Every ledger row pins `model_version`, `prompt_version`, `policy_version`, and `actor_type` ∈ {SYSTEM, AGENT, HUMAN, POLICY}.

## Migrations

- One logical change per Alembic revision; revisions reversible (provide `downgrade`). Seed data for the synthetic ERP lives in the Compose `seed` service (Faker, seed-pinned, versioned) — not in migrations.
- Changing a column type that holds ledger-referenced data = data-lineage event: note in the migration docstring and check audit queries still resolve.

## Repositories

- Repository functions take an explicit session; no implicit commits — the caller (or FastAPI dependency) owns the transaction boundary.
- Query helpers are typed and return domain models, not raw rows. N+1-prone patterns get `selectinload` upfront.
- Idempotency: ingestion checks `content_hash` before insert; race-safe via unique constraint + `ON CONFLICT` handling.

## MinIO (raw documents)

- Store the original bytes once under a content-addressed key (`{content_hash}` prefix); reference from `invoices.raw_ref`. Presigned URLs for UI display; never load raw bytes into graph state.

## Testing

- Integration tests use **testcontainers** Postgres (+pgvector image) with migrations applied, so tests exercise real SQL and constraints.
- Factories/fakers pinned by seed; duplicate-detection tests cover exact-hash and pgvector near-dup paths.

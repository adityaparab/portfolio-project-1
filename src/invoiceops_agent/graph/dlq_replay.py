"""DLQ admin CLI: list pending entries, replay from checkpoint (issue #27).

Usage:
    uv run python -m invoiceops_agent.graph.dlq_replay --list
    uv run python -m invoiceops_agent.graph.dlq_replay --dlq-id 3
    uv run python -m invoiceops_agent.graph.dlq_replay --all

Env-driven (seed_erp pattern): the CLI reads INVOICEOPS_* directly — graph
modules never import the API's settings layer. Replay claims the entry
(PENDING -> REPLAYED, audited in the ledger), then re-executes the run from
its last good checkpoint; exactly-once semantics come from the checkpointer.
"""

import argparse
import asyncio
import json
import logging
import os
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.dlq import DLQService
from invoiceops_agent.graph.runner import GraphRunner
from invoiceops_agent.graph.runtime import build_context, build_gateway, utc_now
from invoiceops_agent.storage.minio import MinioObjectStore

logger = logging.getLogger(__name__)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return value


async def _build(dsn: str) -> tuple[GraphRunner, DLQService, Any]:
    from invoiceops_agent.api.settings import Settings  # CLI edge: config read once

    settings = Settings()
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = MinioObjectStore.from_settings(settings, bucket=settings.minio_bucket)
    gateway = build_gateway(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        token_budgets=settings.gateway_token_budgets,
        timeout_seconds=settings.gateway_timeout_seconds,
        infra_retries=settings.gateway_infra_retries,
    )
    context = build_context(sessions=sessions, store=store, gateway=gateway, clock=utc_now)
    saver, _conn = await open_saver(settings.alembic_dsn or dsn)
    runner = GraphRunner(context, saver)
    return runner, DLQService(), engine


async def main_async(args: argparse.Namespace) -> int:
    dsn = args.dsn or _env(
        "INVOICEOPS_DATABASE_DSN", "postgresql://invoiceops:invoiceops@localhost:5432/invoiceops"
    )
    runner, dlq, engine = await _build(dsn)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    exit_code = 0
    try:
        if args.list or (not args.dlq_id and not args.all):
            pending = await dlq.pending(sessions)
            print(json.dumps(pending, indent=2))
            return 0
        targets = (
            [args.dlq_id] if args.dlq_id else [e["dlq_id"] for e in await dlq.pending(sessions)]
        )
        for dlq_id in targets:
            result = await dlq.replay(sessions, runner, dlq_id)
            print(json.dumps(result, indent=2))
    finally:
        await engine.dispose()
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: env)")
    parser.add_argument("--dlq-id", type=int, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()

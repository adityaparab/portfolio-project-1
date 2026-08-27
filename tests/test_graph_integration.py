"""Integration tests: graph runs with Postgres checkpointing (issue #8 AC).

Requires Docker (testcontainers). Verifies the happy-path traversal,
checkpoint persistence in the `graph` schema, and resume-after-interrupt
without duplicated state entries.
"""

import asyncio
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from testcontainers.postgres import PostgresContainer

from invoiceops_agent.graph.builder import build_graph
from invoiceops_agent.graph.state import GraphState


def _container(monkeypatch: pytest.MonkeyPatch) -> PostgresContainer:
    pg = PostgresContainer("pgvector/pgvector:pg16")
    pg.start()
    url = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")
    monkeypatch.setenv("INVOICEOPS_DATABASE_DSN", url)
    command.upgrade(Config("alembic.ini"), "head")  # creates `graph` schema (0003)
    return pg


async def _checkpoint_count(
    conn: psycopg.AsyncConnection[dict[str, Any]],
) -> int:
    async with conn.cursor() as cur:
        await cur.execute("SELECT count(*) FROM graph.checkpoints")
        row = await cur.fetchone()
    return int(row["count"]) if row else 0


@pytest.mark.integration
def test_happy_path_persists_checkpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    pg = _container(monkeypatch)
    try:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")

        async def run() -> tuple[GraphState, int]:
            conn = await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options="-c search_path=graph", row_factory=dict_row
            )
            try:
                saver = AsyncPostgresSaver(conn)
                await saver.setup()
                graph = build_graph().compile(checkpointer=saver)
                config: RunnableConfig = {"configurable": {"thread_id": "t1"}}
                result = await graph.ainvoke(
                    GraphState(run_id="it-1", content_hash="hash-it-1"), config
                )
                count = await _checkpoint_count(conn)
                return GraphState.model_validate(result), count
            finally:
                await conn.close()

        final, cp_count = asyncio.run(run())

        assert final.node_trace == [
            "ingest",
            "extract",
            "validate",
            "match3way",
            "policy",
            "gate",
            "auto_approve",
            "archive",
        ]
        assert final.route is not None and final.route.value == "AUTO"
        assert cp_count >= len(final.node_trace)  # a checkpoint per node
    finally:
        pg.stop()


@pytest.mark.integration
def test_resume_after_interrupt_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    pg = _container(monkeypatch)
    try:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")

        async def run() -> GraphState:
            conn = await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options="-c search_path=graph", row_factory=dict_row
            )
            try:
                saver = AsyncPostgresSaver(conn)
                await saver.setup()
                graph = build_graph().compile(checkpointer=saver, interrupt_before=["auto_approve"])
                config: RunnableConfig = {"configurable": {"thread_id": "t2"}}
                paused = await graph.ainvoke(
                    GraphState(run_id="it-2", content_hash="hash-it-2"), config
                )
                paused_state = GraphState.model_validate(paused)
                assert paused_state.node_trace[-1] == "gate"  # paused before approve

                resumed = await graph.ainvoke(None, config)  # resume from checkpoint
                return GraphState.model_validate(resumed)
            finally:
                await conn.close()

        final = asyncio.run(run())

        assert final.node_trace.count("archive") == 1  # no duplicated tail
        assert final.node_trace[-1] == "archive"
        assert final.route is not None and final.route.value == "AUTO"
    finally:
        pg.stop()


@pytest.mark.integration
def test_duplicate_routes_to_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    pg = _container(monkeypatch)
    try:
        dsn = pg.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")

        async def run() -> GraphState:
            conn = await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options="-c search_path=graph", row_factory=dict_row
            )
            try:
                saver = AsyncPostgresSaver(conn)
                await saver.setup()
                graph = build_graph().compile(checkpointer=saver)
                config3: RunnableConfig = {"configurable": {"thread_id": "t3"}}
                result = await graph.ainvoke(
                    GraphState(run_id="it-3", content_hash="hash-it-3", duplicate=True),
                    config3,
                )
                return GraphState.model_validate(result)
            finally:
                await conn.close()

        final = asyncio.run(run())
        assert final.node_trace == ["ingest", "reject"]
        assert final.route is not None and final.route.value == "REJECT"
    finally:
        pg.stop()

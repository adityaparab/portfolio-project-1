"""Postgres checkpointer wiring for the graph.

LangGraph's saver creates its own ``checkpoints``/``checkpoint_writes`` tables,
which would collide with the domain ``checkpoints`` table in ``public``
(ARCHITECTURE §6). The saver therefore runs on a dedicated ``graph`` schema
(created by migration 0003) via the connection's search_path.

The async saver is psycopg-based (langgraph-checkpoint-postgres ≥1.x): pass a
``psycopg.AsyncConnection`` in autocommit mode.
"""

from collections.abc import Mapping

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row

GRAPH_SCHEMA = "graph"


async def open_saver(
    dsn: str,
) -> tuple[AsyncPostgresSaver, psycopg.AsyncConnection[Mapping[str, object]]]:
    """Connect (schema-owner DSN expected for first-time setup) and prepare
    the checkpointer tables idempotently."""
    conn = await psycopg.AsyncConnection.connect(
        dsn.replace("postgresql+asyncpg://", "postgresql://"),
        autocommit=True,
        options=f"-c search_path={GRAPH_SCHEMA}",
        row_factory=dict_row,
    )
    saver = AsyncPostgresSaver(conn)
    await saver.setup()
    return saver, conn

"""Dev CLI: push a fake invoice through the graph with Postgres checkpointing.

Usage:
    uv run python -m invoiceops_agent.graph.demo [--dsn postgresql://...] [--thread demo-1]
Runs against the schema-owner DSN (INVOICEOPS_ALEMBIC_DSN / INVOICEOPS_DATABASE_DSN)
because first-run setup creates the saver tables in the `graph` schema.
"""

import argparse
import asyncio

from invoiceops_agent.api.settings import Settings
from invoiceops_agent.graph.builder import compile_graph
from invoiceops_agent.graph.checkpoint import open_saver
from invoiceops_agent.graph.state import GraphState


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help="schema-owner Postgres DSN")
    parser.add_argument("--thread", default="demo-1", help="checkpoint thread id")
    args = parser.parse_args()

    settings = Settings()
    dsn = args.dsn or settings.alembic_dsn or settings.database_dsn

    saver, conn = await open_saver(dsn)
    try:
        graph = compile_graph(checkpointer=saver)
        state = GraphState(run_id="demo-run", content_hash="demo-hash-0001")
        result = await graph.ainvoke(state, {"configurable": {"thread_id": args.thread}})
        final = GraphState.model_validate(result)
        print(f"node_trace: {' → '.join(final.node_trace)}")
        print(f"route:      {final.route}")

        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM graph.checkpoints")
            row = await cur.fetchone()
        cp_count = int(str(row["count"])) if row else 0
        print(f"checkpoints persisted (thread {args.thread!r}): {cp_count}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

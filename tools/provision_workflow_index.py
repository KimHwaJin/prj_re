"""Provision the configured model's partial HNSW index outside API request paths.

Run after alembic -c alembic.crud.ini upgrade head. No example dimension or
provider is assumed. Changing model space requires provisioning and reindexing.
This operation uses CREATE INDEX CONCURRENTLY; deployment role needs DDL rights.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from service_settings import get_settings


async def main():
    settings = get_settings()
    search = settings.workflow_search
    if not search.configured:
        raise SystemExit(
            "Configure WORKFLOW_EMBEDDING_BASE_URL, MODEL and DIMENSIONS first"
        )
    engine = create_async_engine(
        settings.api.database_url, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as conn:
            version = await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            if not version or tuple(map(int, version.split(".")[:2])) < (0, 8):
                raise SystemExit("pgvector >=0.8.0 is required")
            existing = await conn.scalar(
                text(
                    "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname=:name"
                ),
                {"name": search.index_name},
            )
            if existing is False:
                await conn.execute(text("DROP INDEX CONCURRENTLY " + search.index_name))
            await conn.execute(
                text(
                    search.index_sql().replace(
                        "CREATE INDEX IF NOT EXISTS",
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
                    )
                )
            )
        print("Provisioned " + search.index_name)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

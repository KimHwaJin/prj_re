"""Initialize only the local Compose databases before starting API workers."""

import asyncio
import os
import subprocess
import sys
from urllib.parse import urlsplit, unquote
import psycopg
from psycopg import sql

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from dtest.settings.loader import get_settings


def resolved_targets():
    settings = get_settings()
    return {
        "DATABASE_URL": settings.database.database_url,
        "CHECKPOINT_DB_URI": settings.agent.checkpoint_db_uri,
        "EW_DATABASE_URL": settings.worker.database_url,
    }


def validate_local_targets():
    expected = {
        "DATABASE_URL": "chat_app",
        "CHECKPOINT_DB_URI": "agent",
        "EW_DATABASE_URL": {"agent", "chat_app"},
    }
    targets = resolved_targets()
    for name, database in expected.items():
        parsed = urlsplit(targets[name] or "")
        allowed = {database} if isinstance(database, str) else database
        if parsed.hostname != "postgres" or parsed.path.lstrip("/") not in allowed:
            raise RuntimeError(f"{name} must point at the local Compose allowed database")


def provision_local_logins():
    """Keep the existing local volume, adding the credentials selected by the service YAML."""
    credentials = {}
    for url in resolved_targets().values():
        parsed = urlsplit(url)
        username, password = unquote(parsed.username), unquote(parsed.password)
        if username in credentials and credentials[username] != password:
            raise RuntimeError("Conflicting passwords for the same local PostgreSQL role")
        credentials[username] = password
    with psycopg.connect(os.environ["LOCAL_BOOTSTRAP_DATABASE_URL"], autocommit=True) as conn:
        for username, password in credentials.items():
            if username == "dtest":
                continue  # Keep the infrastructure admin password unchanged.
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (username,)).fetchone()
            operation = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(operation + " ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(username), sql.Literal(password)))
            conn.execute(sql.SQL("GRANT dtest TO {}").format(sql.Identifier(username)))


async def setup_checkpoint():
    async with AsyncPostgresSaver.from_conn_string(get_settings().agent.checkpoint_db_uri) as saver:
        await saver.setup()


if __name__ == "__main__":
    validate_local_targets()
    provision_local_logins()
    subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.crud.ini", "upgrade", "head"], check=True)
    subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], check=True)
    asyncio.run(setup_checkpoint())
    print("Local migrations complete for selected CRUD, event and checkpoint targets", flush=True)

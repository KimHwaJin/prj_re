"""Show actual local DB identities without printing connection credentials."""

import json
import os
import socket
from urllib.parse import urlsplit

import psycopg
from redis import Redis

from service_settings import get_settings


service = get_settings()
settings, agent = service.api, service.agent
for role, url in [
    ("crud", settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)),
    ("checkpoint", agent.checkpoint_db_uri),
    ("workflow", service.workflow_database_url),
    ("event", service.worker.database_url),
]:
    if url is None:
        print(json.dumps({"role": role, "enabled": False}))
        continue
    with psycopg.connect(url, connect_timeout=5) as conn:
        row = conn.execute("SELECT current_database(), current_schema()").fetchone()
    print(json.dumps({"role": role, "host": urlsplit(url).hostname, "database": row[0], "schema": row[1]}))
with Redis.from_url(service.worker.redis_url, socket_connect_timeout=5) as client:
    assert client.ping()
print(json.dumps({"worker_host": socket.gethostname(), "checkpointer": settings.graph_checkpointer,
                  "redis": "ready", "redis_host": urlsplit(service.worker.redis_url).hostname,
                  "revision": os.environ.get("SOURCE_REVISION"),
                  "executor_submit_enabled": agent.executor_submit_enabled}))

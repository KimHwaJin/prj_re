"""Fresh PostgreSQL: startup race, data preservation and failure gate."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import yaml
from sqlalchemy.engine import make_url

from dtest.infrastructure.database.schema import SCHEMA_LOCK_KEY

ROOT = Path(__file__).resolve().parents[2]
CHILD = """
import json, logging, sys
from pathlib import Path
from fastapi.testclient import TestClient
from dtest.bootstrap import create_app
from dtest.settings.loader import load_settings
logging.basicConfig(level=logging.INFO)
settings = load_settings(config_path=Path(sys.argv[1]), environ={})
with TestClient(create_app(settings)) as client:
    response = client.get('/service/ready')
    assert response.status_code == 200, response.text
    Path(sys.argv[2]).write_text(json.dumps(response.json()))
"""


def scalar(connection, query, parameters=None):
    row = connection.execute(query, parameters).fetchone()
    assert row is not None
    return row[0]


def test_concurrent_startup_preserves_data_and_gates_failures(tmp_path):
    raw = os.getenv("STARTUP_TEST_DATABASE_URL")
    checkpoint = os.getenv("STARTUP_TEST_CHECKPOINT_URI")
    if not raw or not checkpoint:
        pytest.skip("Requires fresh disposable startup109 PostgreSQL DBs")
    for uri in (raw, checkpoint):
        url = make_url(uri)
        if url.host not in {"127.0.0.1", "localhost"} or not (
            url.database or ""
        ).startswith("startup109_"):
            pytest.fail("Only isolated local startup109_* DBs are allowed")
    primary = (
        make_url(raw)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    values = {
        "DATABASE_URL": make_url(raw)
        .set(drivername="postgresql+asyncpg")
        .render_as_string(hide_password=False),
        "CHECKPOINT_DB_URI": checkpoint,
        "DB_INIT_ON_START": True,
        "AGENT_WORKER_ENABLED": False,
        "EVENT_WORKER_ENABLED": False,
        "TASK_RECONCILER_ENABLED": False,
        "ACTIVE_TRACE": False,
        "MODEL_PROVIDER": "mock",
    }
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump(values))
    config.chmod(0o600)
    processes = []
    logs = []

    def spawn(name, source=config):
        ready = tmp_path / (name + ".json")
        log = (tmp_path / (name + ".log")).open("w")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(source), str(ready)],
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdout=log,
            stderr=log,
        )
        processes.append(process)
        return process, ready

    def wait_success(process, ready):
        assert process.wait(timeout=40) == 0, list(tmp_path.glob("*.log"))
        assert json.loads(ready.read_text())["ready"] is True
        output = ready.with_suffix(".log").read_text()
        assert "service_started" in output
        if ready.stem != "disabled":
            assert "schema_initialization_completed" in output

    try:
        with psycopg.connect(primary, autocommit=True) as connection:
            assert (
                scalar(connection, "SELECT to_regclass('agent_runs')") is None
            ), "Use a fresh isolated test DB"
            connection.execute(
                "SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_KEY,)
            )
            try:
                first = spawn("first")
                second = spawn("second")
                end = time.monotonic() + 15
                while True:
                    waiting = scalar(
                        connection,
                        "SELECT count(*) FROM pg_locks WHERE "
                        "locktype='advisory' AND NOT granted AND objid=%s",
                        (SCHEMA_LOCK_KEY,),
                    )
                    if waiting == 2:
                        break
                    assert all(p.poll() is None for p, _ in (first, second))
                    assert time.monotonic() < end, (
                        "Both apps must wait for lock"
                    )
                    time.sleep(0.05)
                assert not first[1].exists() and not second[1].exists()
            finally:
                connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,)
                )
            wait_success(*first)
            wait_success(*second)
            for table in ("users", "agent_commands", "store", "ew_inbox"):
                assert scalar(connection, "SELECT to_regclass(%s)", (table,))
            uid = uuid4()
            connection.execute(
                "INSERT INTO users(user_id,user_name,public_user_id) "
                "VALUES (%s,'preserved','startup-preserved')",
                (uid,),
            )
            wait_success(*spawn("restart"))
            assert (
                scalar(
                    connection,
                    "SELECT user_name FROM users WHERE user_id=%s",
                    (uid,),
                )
                == "preserved"
            )
        with psycopg.connect(checkpoint) as connection:
            for table in (
                "checkpoints",
                "checkpoint_blobs",
                "checkpoint_writes",
            ):
                assert scalar(connection, "SELECT to_regclass(%s)", (table,))

        broken = {
            **values,
            "DATABASE_URL": make_url(values["DATABASE_URL"])
            .set(port=1)
            .render_as_string(hide_password=False),
        }
        invalid = tmp_path / "invalid.yml"
        invalid.write_text(yaml.safe_dump(broken))
        invalid.chmod(0o600)
        failure, ready = spawn("failure", invalid)
        assert failure.wait(timeout=20) != 0
        assert not ready.exists()
        assert (
            "API/Workers were not started"
            in (tmp_path / "failure.log").read_text()
        )
        broken["DB_INIT_ON_START"] = False
        invalid.write_text(yaml.safe_dump(broken))
        wait_success(*spawn("disabled", invalid))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        for log in logs:
            log.close()

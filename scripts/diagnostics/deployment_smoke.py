"""Validate one app.py process with fresh, isolated Docker PostgreSQL/Redis.

Never reads .env or calls a real model/Executor. Deletes only the unique Compose
project created by this invocation. Build the requested image beforehand.
"""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--split-event-db",
        action="store_true",
        help="Preserve legacy agent Event DB override",
    )
    args = parser.parse_args()
    project = "dtest-config-check-" + uuid4().hex[:10]
    init_sql = ROOT / "scripts/local/init-databases.sql"
    if not init_sql.is_file():
        raise RuntimeError("Local DB initialization SQL is missing")
    env = {
        "APP_ENV": "dev",
        "SERVICE_CONFIG_FILE": "/app/config.diagnostic.yml",
        "DATABASE_URL": "postgresql+asyncpg://dtest:smoke-local@postgres:5432/chat_app",
        "CHECKPOINT_DB_URI": "postgresql://dtest:smoke-local@postgres:5432/agent",
        "REDIS_URL": "redis://redis:6379/0",
        "MODEL_PROVIDER": "mock",
        "MODEL_NAME": "deployment-smoke",
        "EXECUTOR_BASE_URL": "http://unused-executor:8000",
        "EXECUTOR_SUBMIT_ENABLED": "false",
        "EVENT_WORKER_ENABLED": "true",
        "EW_HEALTH_PORT": "0",
        "EW_NAMESPACE": project,
        "AGENT_WORKER_CONCURRENCY": "2",
        "SHUTDOWN_DRAIN_SECONDS": "1",
        "SHUTDOWN_TIMEOUT_SECONDS": "5",
        "LOCAL_BOOTSTRAP_DATABASE_URL": "postgresql://dtest:smoke-local@postgres:5432/postgres",
    }
    if args.split_event_db:
        env["EW_DATABASE_URL"] = (
            "postgresql://dtest:smoke-local@postgres:5432/agent"
        )
    app = {"image": args.image, "environment": env}
    healthy = lambda command: {
        "test": command,
        "interval": "1s",
        "timeout": "3s",
        "retries": 30,
    }
    definition = {
        "name": project,
        "services": {
            "postgres": {
                "image": "pgvector/pgvector:0.8.6-pg17",
                "environment": {
                    "POSTGRES_USER": "dtest",
                    "POSTGRES_PASSWORD": "smoke-local",
                    "POSTGRES_DB": "chat_app",
                },
                "volumes": [
                    str(init_sql)
                    + ":/docker-entrypoint-initdb.d/01-agent.sql:ro"
                ],
                "healthcheck": healthy(
                    ["CMD-SHELL", "pg_isready -U dtest -d chat_app"]
                ),
            },
            "redis": {
                "image": "redis:7.4-alpine",
                "healthcheck": healthy(["CMD", "redis-cli", "ping"]),
            },
            "migrate": {
                **app,
                "command": ["python", "scripts/local/bootstrap.py"],
                "depends_on": {"postgres": {"condition": "service_healthy"}},
            },
            "api": {
                **app,
                "command": ["python", "app.py"],
                "ports": ["127.0.0.1::8000"],
                "stop_grace_period": "15s",
                "depends_on": {
                    "migrate": {"condition": "service_completed_successfully"},
                    "redis": {"condition": "service_healthy"},
                },
                "healthcheck": healthy(
                    [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://localhost:8000/service/ready',timeout=3)",
                    ]
                ),
            },
        },
    }
    report = {
        "project": project,
        "image": args.image,
        "split_event_db": args.split_event_db,
        "scope": (
            "startup/readiness/metrics/SSO boundary/idle SIGTERM; no "
            "LLM or Executor "
            "calls"
        ),
    }

    def run(command):
        return subprocess.run(
            command, capture_output=True, text=True, check=True
        ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="dtest-config-smoke-") as folder:
        manifest = Path(folder) / "compose.yaml"
        manifest.write_text(yaml.safe_dump(definition, sort_keys=False))
        compose = ["docker", "compose", "-p", project, "-f", str(manifest)]
        container = project + "-api-1"
        try:
            run(
                compose + ["up", "-d", "--wait", "--wait-timeout", "90", "api"]
            )
            port = int(
                run(compose + ["port", "api", "8000"]).rsplit(":", 1)[1]
            )
            base = f"http://127.0.0.1:{port}"
            report["docker_top"] = run(
                ["docker", "top", container, "-eo", "pid,args"]
            )
            assert len(report["docker_top"].splitlines()) == 2
            report["http"] = {}
            for path in [
                "/health",
                "/service/ready",
                "/service/live",
                "/openapi.json",
            ]:
                with urlopen(base + path, timeout=5) as response:
                    data = json.load(response)
                    report["http"][path] = response.status
                    if path == "/openapi.json":
                        assert (
                            "/api/v1/sessions/{session_id}/runs"
                            in data["paths"]
                        )
            with urlopen(base + "/service/metrics", timeout=5) as response:
                text = response.read().decode()
                assert "ew_operations" in text and "python_info" in text
                report["metrics_status"] = response.status
            try:
                urlopen(
                    Request(
                        base + "/api/v1/users/me",
                        headers={"X-User-Id": "not-a-login"},
                    ),
                    timeout=5,
                )
                raise AssertionError("Expected SSO enforcement")
            except HTTPError as exc:
                assert exc.code == 401
                report["sso_without_cookie_status"] = exc.code
            summary = json.loads(
                run(
                    compose
                    + [
                        "exec",
                        "-T",
                        "api",
                        "python",
                        "app.py",
                        "--check-config",
                    ]
                )
            )
            report["effective_config"] = {
                k: v for k, v in summary.items() if k != "settings_sources"
            }
            report["db_identities"] = [
                json.loads(line)
                for line in run(
                    compose
                    + [
                        "exec",
                        "-T",
                        "api",
                        "python",
                        "scripts/local/inspect_environment.py",
                    ]
                ).splitlines()
            ]
            event_db = next(
                item["database"]
                for item in report["db_identities"]
                if item.get("role") == "event"
            )
            assert event_db == ("agent" if args.split_event_db else "chat_app")
            run(compose + ["stop", "redis"])
            try:
                urlopen(base + "/service/ready", timeout=6)
                raise AssertionError("Readiness remained green without Redis")
            except HTTPError as exc:
                assert exc.code == 503
                report["redis_outage_ready_status"] = exc.code
            with urlopen(base + "/service/live", timeout=5) as response:
                assert response.status == 200
                report["redis_outage_live_status"] = response.status
            run(compose + ["start", "redis"])
            for _ in range(30):
                try:
                    with urlopen(base + "/service/ready", timeout=5):
                        report["redis_recovery_ready_status"] = 200
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                raise AssertionError("Readiness did not recover")
            started = time.monotonic()
            run(compose + ["stop", "api"])
            report["sigterm_seconds"] = round(time.monotonic() - started, 3)
            state = json.loads(
                run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{json .State}}",
                        container,
                    ]
                )
            )
            report["exit_code"] = state["ExitCode"]
            assert state["ExitCode"] == 0 and not state["OOMKilled"]
            logs = run(compose + ["logs", "--no-log-prefix", "api"])
            assert (
                "service_draining" in logs
                and "Application shutdown complete" in logs
            )
            report["shutdown_logs"] = [
                line
                for line in logs.splitlines()
                if any(
                    k in line
                    for k in [
                        "service_draining",
                        "Shutting down",
                        "Application shutdown complete",
                        "Finished server process",
                    ]
                )
            ]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "output": str(args.output),
                        "split_event_db": args.split_event_db,
                        "sigterm_seconds": report["sigterm_seconds"],
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            # This project name is freshly generated; never targets an existing stack.
            run(compose + ["down", "-v", "--remove-orphans"])


if __name__ == "__main__":
    main()

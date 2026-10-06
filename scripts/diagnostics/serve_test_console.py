"""Serve the standalone test HTML on the real app, loopback only.

Normal mode retains supplied service configuration and corporate SSO.
--test-login substitutes employee verification in isolated local databases only.
--model chooses configured, fixture or real independently of test login.
--local-fixtures is the legacy test-login + fixture-model shorthand.
--temporary-db owns an ephemeral Docker PostgreSQL.
Nothing here is imported by the production application.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import asyncio
import json
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from dtest.api_service.web.console import render_console
import uvicorn

from cookie_auth import install_employee_fixture
from verify_api_contract_flow import settings_for_test, migrate
from dtest.settings.loader import load_settings
from dtest.bootstrap import create_app
from model_connection import (
    load_model_env,
    validate_real_model,
    model_host_alias,
)

ROOT = Path(__file__).resolve().parents[2]


class ConsoleServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Do not re-raise SIGINT inside asyncio.run before diagnostic cleanup.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.handle_exit, sig, None)
        try:
            yield
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1500:])
    return result.stdout.strip()


def temporary_database(args, name):
    command(
        "docker",
        "run",
        "--rm",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_USER=console_test",
        "-e",
        "POSTGRES_PASSWORD=console_test_only",
        "-p",
        f"127.0.0.1:{args.db_port}:5432",
        "pgvector/pgvector:0.8.6-pg17",
    )
    for _ in range(60):
        try:
            command(
                "docker",
                "exec",
                name,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "console_test",
            )
            break
        except RuntimeError:
            time.sleep(0.25)
    else:
        raise RuntimeError("Temporary PostgreSQL did not become ready")
    for db in ("agentic_runtime_test", "agentic_checkpoint_test"):
        command(
            "docker",
            "exec",
            name,
            "createdb",
            "-h",
            "127.0.0.1",
            "-U",
            "console_test",
            db,
        )
    prefix = f"postgresql+asyncpg://console_test:console_test_only@127.0.0.1:{args.db_port}/"
    return {
        "DATABASE_URL": prefix + "agentic_runtime_test",
        "CHECKPOINT_DB_URI": prefix.replace("+asyncpg", "")
        + "agentic_checkpoint_test",
    }


def prepare_settings(args, config, namespace):
    if args.test_login:
        config = settings_for_test(
            config, namespace, args.port, model_fixture=args.model == "fixture"
        )
        config.update(
            SSO_COOKIE_NAME=f"dtest_test_console_{args.port}",
            SSO_ALLOWED_RETURN_ROOTS=["/test-console"],
            EXECUTOR_SUBMIT_ENABLED=args.executor == "real",
        )
    if args.model == "real":
        if args.model_env:
            # Import model keys only, never the old DB/streams/SSO environment.
            config.update(load_model_env(args.model_env))
        validate_real_model(config)
    return config


def public_runtime(args, settings):
    return {
        "apiBase": f"http://127.0.0.1:{args.port}{settings.api.api_v1_prefix}",
        "openapiUrl": "/openapi.json",
        "returnTo": "/test-console",
        "auth": {
            "mode": "fixture" if args.test_login else "configured",
            "role": ("admin" if args.fixture_admin else "user")
            if args.test_login
            else None,
        },
        "model": {
            "mode": args.model,
            "name": settings.agent.model_name,
            "delay_ms": args.model_delay_ms
            if args.model == "fixture"
            else None,
        },
        "executor": {
            "mode": "real" if settings.agent.executor_submit_enabled else "off"
        },
    }


async def serve(args, config, namespace):
    config = prepare_settings(args, config, namespace)
    if args.test_login:
        migrate(config)
    if args.model == "fixture":
        from verify_api_contract_flow import install_model_fixture

        install_model_fixture(
            {"model_delay_ms": args.model_delay_ms},
            {"models": []},
            lambda: True,
        )
    settings = load_settings(config=config, environ={})
    app = create_app(settings)
    if args.test_login:
        install_employee_fixture(app, namespace)
        if args.fixture_admin:
            from dtest.application.admin import bootstrap

            await bootstrap(namespace, "Test console administrator")
    runtime = public_runtime(args, settings)

    # This runtime descriptor contains labels only, no credentials or endpoints.
    @app.get("/test-console", include_in_schema=False)
    async def console():
        return await render_console(runtime)

    print(f"Console: http://127.0.0.1:{args.port}/test-console", flush=True)
    print(
        json.dumps(
            {
                "auth": runtime["auth"],
                "model": runtime["model"],
                "executor": runtime["executor"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.model == "fixture":
        print(
            (
                "Prefix [answer] for fixed-model answers; other input "
                "selects the quality "
                "plan."
            ),
            flush=True,
        )
    server = ConsoleServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
        )
    )
    try:
        await server.serve()
    finally:
        if args.model == "fixture":
            from dtest.agent_service.agents.analysis.planning import (
                runtime as planning_runtime,
            )

            for client in getattr(
                planning_runtime, "_benchmark_fixture_clients", []
            ):
                await client.aclose()
        if args.test_login:
            from redis.asyncio import Redis

            redis = Redis.from_url(config["REDIS_URL"])
            try:
                await redis.xgroup_destroy(
                    settings.worker.executor_event_stream,
                    settings.worker.event_group,
                )
                keys = [
                    key
                    async for key in redis.scan_iter(match=namespace + ":*")
                ]
                if keys:
                    await redis.delete(*keys)
            finally:
                await redis.aclose()


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--settings-file",
        type=Path,
        help="Private flat service JSON, not .env",
    )
    parser.add_argument(
        "--test-login",
        action="store_true",
        help="Local employee-verdict fixture; cookies/CSRF/DB are real",
    )
    parser.add_argument(
        "--local-fixtures",
        action="store_true",
        help="Legacy shorthand: --test-login --model fixture",
    )
    parser.add_argument("--model", choices=["configured", "fixture", "real"])
    parser.add_argument(
        "--model-env",
        type=Path,
        help="Real-mode model-only keys from a private .env",
    )
    parser.add_argument(
        "--fixture-admin",
        action="store_true",
        help="Synthetic admin, temporary fixture DB only",
    )
    parser.add_argument(
        "--temporary-db",
        action="store_true",
        help="Owned Docker PG17, removed on exit",
    )
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument("--db-port", type=int, default=53601)
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--executor-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--executor-shared-root", type=Path)
    parser.add_argument(
        "--executor",
        choices=["off", "real"],
        help="Test login only; normal mode retains config",
    )
    parser.add_argument("--model-delay-ms", type=int, default=300)
    args = parser.parse_args(argv)
    if args.local_fixtures:
        if args.model not in (None, "fixture"):
            parser.error(
                "--local-fixtures means fixture model; use "
                "--test-login --model "
                "real"
            )
        args.test_login = True
        args.model = "fixture"
    args.model = args.model or ("fixture" if args.test_login else "configured")
    if args.fixture_admin and not (args.test_login and args.temporary_db):
        parser.error("--fixture-admin requires --test-login --temporary-db")
    if args.temporary_db and (not args.test_login or args.settings_file):
        parser.error(
            "--temporary-db requires --test-login and no --settings-file"
        )
    if not args.temporary_db and not args.settings_file:
        parser.error("Supply --settings-file or --test-login --temporary-db")
    if args.model == "fixture" and not args.test_login:
        parser.error("Fixture model requires isolated --test-login")
    if args.model_env and args.model != "real":
        parser.error("--model-env requires --model real")
    if args.model == "real" and not (args.model_env or args.settings_file):
        parser.error("Real mode requires --model-env or --settings-file")
    if args.temporary_db and args.model == "configured":
        parser.error("Temporary DB mode requires --model fixture or real")
    if args.executor and not args.test_login:
        parser.error(
            "--executor requires --test-login; configured mode "
            "retains service "
            "settings"
        )
    if args.test_login:
        args.executor = args.executor or "off"
    if args.model_delay_ms < 0 or not all(
        1 <= p <= 65535 for p in (args.port, args.db_port)
    ):
        parser.error("Invalid delay or port")
    return args


def main():
    args = arguments()
    namespace = "test-console-" + uuid4().hex[:12]
    container = "dtest-" + namespace
    workspace = (
        tempfile.TemporaryDirectory(prefix=namespace + "-")
        if args.temporary_db
        else None
    )
    try:
        if args.temporary_db:
            config = temporary_database(args, container)
            config.update(
                REDIS_URL=args.redis_url,
                EXECUTOR_BASE_URL=args.executor_base_url,
                WORKFLOW_STORAGE_ROOT=str(Path(workspace.name) / "workflows"),
                ANALYSIS_DATASETS={
                    "default-nce": {
                        "title": "NCE local sample",
                        "scope": "GLOBAL",
                        "runtime_path": (
                            "/workspace/pv/default_data/df_nce_long_form"
                            "at.parquet"
                        ),
                    }
                },
            )
        else:
            config = json.loads(args.settings_file.read_text())
        if args.executor_shared_root:
            config["EXECUTOR_SHARED_RESULT_ROOT"] = str(
                args.executor_shared_root.resolve()
            )
        if (
            args.test_login
            and args.executor == "real"
            and not config.get("EXECUTOR_SHARED_RESULT_ROOT")
        ):
            raise ValueError(
                "Actual Executor requires --executor-shared-root or "
                "EXECUTOR_SHARED_RESULT_ROOT"
            )
        with model_host_alias() if args.model == "real" else nullcontext():
            asyncio.run(serve(args, config, namespace))
    finally:
        if args.temporary_db:
            # Exact diagnostic-owned container only; existing Compose is untouched.
            subprocess.run(
                ["docker", "stop", container], capture_output=True, text=True
            )
        if workspace:
            workspace.cleanup()


if __name__ == "__main__":
    main()

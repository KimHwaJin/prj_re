#!/usr/bin/env python3
"""Create and update the isolated local Docker environment."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.local"
APP_CONFIG = ROOT / "workspace/config.compose.yml"


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if (
                not line.strip()
                or line.lstrip().startswith("#")
                or "=" not in line
            ):
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def initialize(profile="local", config_path=None):
    # Resolve once with exactly the same loader as app.py. No implicit .env read.
    sys.path.insert(0, str(ROOT / "src"))
    import yaml
    from dtest.settings.loader import load_settings
    from dtest.settings.files import write_private, yaml_document

    service = load_settings(
        root=ROOT, profile=profile, config_path=config_path
    )
    source = dict(service.inputs)
    previous = read_env(ENV_FILE)
    values = {
        key: previous.get(key, default)
        for key, default in read_env(ROOT / ".env.local.example").items()
        if key.startswith("LOCAL_")
    }
    values["LOCAL_POSTGRES_PASSWORD"] = previous.get(
        "LOCAL_POSTGRES_PASSWORD"
    ) or secrets.token_hex(24)
    targets = {
        "DATABASE_URL": service.database.database_url,
        "CHECKPOINT_DB_URI": service.agent.checkpoint_db_uri,
        "EW_DATABASE_URL": service.worker.database_url,
    }
    for key, value in targets.items():
        if value is None:
            continue
        parsed = urlsplit(value)
        if not parsed.username or not parsed.password:
            raise RuntimeError(
                key + " must be a PostgreSQL URL with credentials"
            )
        credentials = parsed.netloc.rsplit("@", 1)[0]
        # Preserve role, DB and query. All Compose DB connections use internal port5432.
        source[key] = urlunsplit(
            parsed._replace(netloc=credentials + "@postgres:5432")
        )
    shared = str(service.agent.executor_shared_input_root)
    if not Path(shared).is_absolute():
        raise RuntimeError(
            "EXECUTOR_SHARED_INPUT_ROOT must be absolute for the "
            "Docker bind "
            "mount"
        )
    if str(service.agent.executor_shared_result_root) != shared:
        raise RuntimeError(
            "Configure separate Docker bind mounts when "
            "input/result roots "
            "differ"
        )
    values["LOCAL_SHARED_INPUT_ROOT"] = shared
    values["LOCAL_APP_ENV"] = service.profile
    source.update(SERVER_HOST="0.0.0.0", SERVER_PORT=8000)
    write_private(
        APP_CONFIG,
        "# Private resolved service settings for local Compose.\n"
        + yaml.safe_dump(
            yaml_document(source), allow_unicode=True, sort_keys=False
        ),
        overwrite=True,
    )
    # The non-root container joins only this file's group; source profile stays0600.
    # Host bind mounts preserve permissions, unlike the old env_file transport.
    os.chmod(APP_CONFIG, 0o640)
    values["LOCAL_CONFIG_GID"] = str(APP_CONFIG.stat().st_gid)
    # Only Compose infrastructure variables remain in dotenv, never application settings.
    content = (
        "# Compose infrastructure only. Service values live in "
        "workspace/config.compose.yml.\n"
    )
    for key, value in values.items():
        content += key + "='" + str(value).replace("'", "\\'") + "'\n"
    write_private(ENV_FILE, content, overwrite=True)
    print(
        "Prepared private YAML from selected service configuration; "
        "local DB host override "
        "applied."
    )


def compose(*args):
    env = dict(os.environ)
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    digest = hashlib.sha256()
    sources = [
        ROOT / name
        for name in [
            "Dockerfile",
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "cli.py",
            "run.py",
            "app.py",
            "scripts/configure.py",
            "scripts/migrate.py",
            "config.diagnostic.yml",
            "cicd/basic/dev/config.dev.example.yml",
        ]
    ]
    sources.append(ROOT / "config.example.yml")
    sources.extend(ROOT.glob("config.*.example.yml"))
    for directory in ["src", "migrations", "crud_migrations", "scripts/local"]:
        sources.extend(
            p
            for p in (ROOT / directory).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix != ".pyc"
        )
    for path in sorted(sources):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    env["LOCAL_SOURCE_REVISION"] = (
        (revision.stdout.strip() or "working-tree")
        + "+"
        + digest.hexdigest()[:12]
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ENV_FILE),
            "-f",
            str(ROOT / "compose.local.yaml"),
            *args,
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def retire_legacy_event_worker():
    """Drain only the old standalone service in this local Compose project.

    Merely removing the service definition leaves its old container running.
    Stop before migration so it cannot consume events alongside the embedded Worker.
    """
    project = os.environ.get("COMPOSE_PROJECT_NAME", "dtest-agent-local")
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=" + project,
            "--filter",
            "label=com.docker.compose.service=event-worker",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    containers = result.stdout.split()
    if containers:
        subprocess.run(
            ["docker", "stop", "--time", "70", *containers], check=True
        )
        subprocess.run(["docker", "rm", *containers], check=True)


def smoke():
    port = read_env(ENV_FILE).get("LOCAL_API_PORT", "18000")
    base = "http://127.0.0.1:" + port

    def request(path, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        with urlopen(
            Request(
                base + path,
                data=payload,
                headers={"Content-Type": "application/json"},
            ),
            timeout=10,
        ) as response:
            return json.load(response)

    assert request("/health")["status"] == "ok"
    assert (
        "/api/v1/sessions/{session_id}/runs"
        in request("/openapi.json")["paths"]
    )
    compose(
        "exec", "-T", "api", "python", "scripts/local/inspect_environment.py"
    )
    assert request("/service/ready")["ready"] is True
    print("Ready: " + base + "/demo")
    print("Swagger: " + base + "/docs")
    print("API·Agent·Event Worker ready: " + base + "/service/ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["init", "up", "update", "down", "status", "logs", "smoke"],
    )
    parser.add_argument(
        "--env", choices=["local", "dev", "stg", "prd"], default="local"
    )
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    initialize(args.env, args.config)
    if args.action == "init":
        return
    if args.action in {"up", "update"}:
        compose("build", "api")
        from dtest.settings.loader import load_settings

        local = load_settings(
            config_path=APP_CONFIG, environ={}, profile=args.env
        )
        redis_host = urlsplit(local.redis.redis_url).hostname
        if redis_host == "redis":
            compose(
                "--profile",
                "local-redis",
                "up",
                "-d",
                "--wait",
                "postgres",
                "redis",
            )
        else:
            compose("up", "-d", "--wait", "postgres")
        # Both up and update can encounter an existing environment.
        compose("stop", "api")
        retire_legacy_event_worker()
        compose("run", "--rm", "--no-deps", "migrate")
        compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "api")
        smoke()
    elif args.action == "down":
        compose("down")  # Named volumes intentionally survive shutdown.
    elif args.action == "logs":
        compose("logs", "--tail", "200", "-f", "api")
    elif args.action == "status":
        compose("ps", "-a")
        compose(
            "exec",
            "-T",
            "api",
            "python",
            "scripts/local/inspect_environment.py",
        )
    elif args.action == "smoke":
        smoke()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)

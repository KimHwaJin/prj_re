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
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.local"


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def initialize():
    if not (ROOT / ".env").exists():
        raise RuntimeError("Create .env from .env.example before starting the local environment")
    # Let Compose parse dotenv quoting/interpolation exactly as it will for the API.
    config = json.dumps({"services": {"inspect": {"image": "dtest-agent:local-dev",
                                                "env_file": [str(ROOT / ".env")]}}})
    result = subprocess.run(["docker", "compose", "--project-directory", str(ROOT),
                             "-f", "-", "config", "--format", "json"], input=config,
                            capture_output=True, text=True, check=True)
    source = json.loads(result.stdout)["services"]["inspect"]["environment"]
    previous = read_env(ENV_FILE)
    values = {key: previous.get(key, default) for key, default in
              read_env(ROOT / ".env.local.example").items() if key.startswith("LOCAL_")}
    values["LOCAL_POSTGRES_PASSWORD"] = previous.get("LOCAL_POSTGRES_PASSWORD") or secrets.token_hex(24)
    workers = values.get("LOCAL_API_WORKERS", "4")
    if not workers.isdigit() or int(workers) < 1:
        raise RuntimeError("LOCAL_API_WORKERS must be a positive integer")
    keys = ["DATABASE_URL", "CHECKPOINT_DB_URI", "AGENT_CHECKPOINT_DATABASE_URL", "EW_DATABASE_URL", "WORKFLOW_DATABASE_URL"]
    for key in keys:
        value = source.get(key) or (source.get("EW_DATABASE_URL") if key == "WORKFLOW_DATABASE_URL" else None)
        if not value:
            raise RuntimeError(key + " is required in .env")
        parsed = urlsplit(value)
        if not parsed.username or not parsed.password or parsed.scheme not in {"postgresql", "postgresql+asyncpg", "postgresql+psycopg"}:
            raise RuntimeError(key + " must be a PostgreSQL URL with credentials")
        credentials = parsed.netloc.rsplit("@", 1)[0]
        # Preserve credentials, database, query, scheme and port; replace only host.
        authority = credentials + "@postgres" + (":" + str(parsed.port) if parsed.port else "")
        values["LOCAL_" + key] = urlunsplit(parsed._replace(netloc=authority))
    shared = source.get("EXECUTOR_SHARED_INPUT_ROOT", "/workspace/pv")
    if not Path(shared).is_absolute():
        raise RuntimeError("EXECUTOR_SHARED_INPUT_ROOT must be absolute for the Docker bind mount")
    result_root = source.get("EXECUTOR_SHARED_RESULT_ROOT", shared)
    if result_root != shared:
        raise RuntimeError("Configure separate Docker bind mounts when input/result roots differ")
    values["LOCAL_SHARED_INPUT_ROOT"] = shared
    content = "# Generated from .env: DB host only is replaced with postgres. Do not commit.\n"
    for key, value in values.items():
        content += key + "='" + value.replace("'", "\\'") + "'\n"
    with ENV_FILE.open("w") as output:
        os.chmod(ENV_FILE, 0o600)
        output.write(content)
    print("Synchronized .env settings; only database hosts point to local PostgreSQL.")


def compose(*args):
    env = dict(os.environ)
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True)
    digest = hashlib.sha256()
    sources = [ROOT / name for name in ["Dockerfile", "pyproject.toml", "uv.lock", "README.md", "cli.py", "run.py"]]
    for directory in ["src", "migrations", "crud_migrations", "scripts/local"]:
        sources.extend(p for p in (ROOT / directory).rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for path in sorted(sources):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    env["LOCAL_SOURCE_REVISION"] = (revision.stdout.strip() or "working-tree") + "+" + digest.hexdigest()[:12]
    subprocess.run(["docker", "compose", "--env-file", str(ENV_FILE), "-f",
                    str(ROOT / "compose.local.yaml"), *args], cwd=ROOT, env=env, check=True)


def smoke():
    port = read_env(ENV_FILE).get("LOCAL_API_PORT", "18000")
    base = "http://127.0.0.1:" + port

    def request(path, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        with urlopen(Request(base + path, data=payload, headers={"Content-Type": "application/json"}), timeout=10) as response:
            return json.load(response)

    assert request("/health")["status"] == "ok"
    assert "/api/v1/sessions/{session_id}/runs" in request("/openapi.json")["paths"]
    try:
        request("/api/v1/users/by-name/local-dev")
    except HTTPError as exc:
        if exc.code != 404:
            raise
        request("/api/v1/users", {"user_name": "local-dev"})
    compose("exec", "-T", "api", "python", "scripts/local/inspect_environment.py")
    worker_port = read_env(ENV_FILE).get("LOCAL_EVENT_WORKER_PORT", "18011")
    with urlopen("http://127.0.0.1:" + worker_port + "/health/ready", timeout=5) as response:
        assert response.status == 200 and response.read().strip() == b"ok"
    print("Ready: " + base + "/demo (user: local-dev)")
    print("Swagger: " + base + "/docs")
    print("Event Worker ready: http://127.0.0.1:" + worker_port + "/health/ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "update", "down", "status", "logs", "smoke"])
    args = parser.parse_args()
    initialize()
    if args.action == "init":
        return
    if args.action in {"up", "update"}:
        compose("build", "api")
        compose("up", "-d", "--wait", "postgres")
        if args.action == "update":
            # Let the old process finish/shut down before migrating its database.
            compose("stop", "api", "event-worker")
        compose("run", "--rm", "--no-deps", "migrate")
        compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "api", "event-worker")
        smoke()
    elif args.action == "down":
        compose("down")  # Named volumes intentionally survive shutdown.
    elif args.action == "logs":
        compose("logs", "--tail", "200", "-f", "api", "event-worker")
    elif args.action == "status":
        compose("ps", "-a")
        compose("exec", "-T", "api", "python", "scripts/local/inspect_environment.py")
        compose("exec", "-T", "event-worker", "python", "scripts/local/inspect_environment.py")
    elif args.action == "smoke":
        smoke()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)

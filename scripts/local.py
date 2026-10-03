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
    # Canonicalize old spellings before host overrides; never modify the user's .env.
    sys.path.insert(0, str(ROOT / "src"))
    from service_settings import ALIASES
    source = dict(source)
    for canonical, aliases in ALIASES.items():
        present = [name for name in (canonical, *aliases) if name in source]
        if len({source[name] for name in present}) > 1:
            raise RuntimeError("Conflicting aliases for " + canonical)
        if present:
            source[canonical] = source[present[0]]
        for alias in aliases:
            source.pop(alias, None)
    previous = read_env(ENV_FILE)
    values = {key: previous.get(key, default) for key, default in
              read_env(ROOT / ".env.local.example").items() if key.startswith("LOCAL_")}
    values["LOCAL_POSTGRES_PASSWORD"] = previous.get("LOCAL_POSTGRES_PASSWORD") or secrets.token_hex(24)
    keys = ["DATABASE_URL", "CHECKPOINT_DB_URI", "EW_DATABASE_URL", "WORKFLOW_DATABASE_URL"]
    for key in keys:
        fallback = {"EW_DATABASE_URL": source.get("DATABASE_URL"),
                    "WORKFLOW_DATABASE_URL": source.get("EW_DATABASE_URL") or source.get("DATABASE_URL")}
        value = source.get(key) or fallback.get(key)
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
    content = "# Generated canonical .env + local DB host overrides. Do not commit.\n"
    # One generated file serves both Compose interpolation and env_file.
    # Local infrastructure controls are preserved; service values always come from .env.
    for key, value in {**source, **values}.items():
        content += key + "='" + str(value).replace("'", "\\'") + "'\n"
    with ENV_FILE.open("w") as output:
        os.chmod(ENV_FILE, 0o600)
        output.write(content)
    print("Synchronized canonical .env settings; only database hosts point to local PostgreSQL.")


def compose(*args):
    env = dict(os.environ)
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True)
    digest = hashlib.sha256()
    sources = [ROOT / name for name in ["Dockerfile", "pyproject.toml", "uv.lock", "README.md", "cli.py", "run.py", "app.py"]]
    sources.extend(ROOT.glob("config*.yml"))
    for directory in ["src", "migrations", "crud_migrations", "scripts/local"]:
        sources.extend(p for p in (ROOT / directory).rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for path in sorted(sources):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    env["LOCAL_SOURCE_REVISION"] = (revision.stdout.strip() or "working-tree") + "+" + digest.hexdigest()[:12]
    subprocess.run(["docker", "compose", "--env-file", str(ENV_FILE), "-f",
                    str(ROOT / "compose.local.yaml"), *args], cwd=ROOT, env=env, check=True)


def retire_legacy_event_worker():
    """Drain only the old standalone service in this local Compose project.

    Merely removing the service definition leaves its old container running.
    Stop before migration so it cannot consume events alongside the embedded Worker.
    """
    project = os.environ.get("COMPOSE_PROJECT_NAME", "dtest-agent-local")
    result = subprocess.run(["docker", "ps", "-a",
        "--filter", "label=com.docker.compose.project=" + project,
        "--filter", "label=com.docker.compose.service=event-worker",
        "--format", "{{.ID}}"], capture_output=True, text=True, check=True)
    containers = result.stdout.split()
    if containers:
        subprocess.run(["docker", "stop", "--time", "70", *containers], check=True)
        subprocess.run(["docker", "rm", *containers], check=True)


def smoke():
    port = read_env(ENV_FILE).get("LOCAL_API_PORT", "18000")
    base = "http://127.0.0.1:" + port

    def request(path, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        with urlopen(Request(base + path, data=payload, headers={"Content-Type": "application/json"}), timeout=10) as response:
            return json.load(response)

    assert request("/health")["status"] == "ok"
    assert "/api/v1/sessions/{session_id}/runs" in request("/openapi.json")["paths"]
    compose("exec", "-T", "api", "python", "scripts/local/inspect_environment.py")
    assert request("/service/ready")["ready"] is True
    print("Ready: " + base + "/demo")
    print("Swagger: " + base + "/docs")
    print("API·Agent·Event Worker ready: " + base + "/service/ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "update", "down", "status", "logs", "smoke"])
    args = parser.parse_args()
    initialize()
    if args.action == "init":
        return
    if args.action in {"up", "update"}:
        compose("build", "api")
        redis_host = urlsplit(read_env(ENV_FILE).get("REDIS_URL", "")).hostname
        if redis_host == "redis":
            compose("--profile", "local-redis", "up", "-d", "--wait", "postgres", "redis")
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
        compose("exec", "-T", "api", "python", "scripts/local/inspect_environment.py")
    elif args.action == "smoke":
        smoke()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)

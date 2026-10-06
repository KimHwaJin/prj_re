"""Local diagnostic mode isolation; no databases/model/Executor connections."""

from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_connection import load_model_env, model_host_alias
from serve_test_console import arguments, prepare_settings, public_runtime
from dtest.settings.loader import load_settings


def local_config():
    return {
        "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:5432/agentic_runtime_test",
        "CHECKPOINT_DB_URI": "postgresql://test:test@127.0.0.1:5432/agentic_checkpoint_test",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "EXECUTOR_BASE_URL": "http://127.0.0.1:8000",
        "MODEL_PROVIDER": "openai_compatible",
        "MODEL_NAME": "actual-model",
        "API_BASE_URL": "http://model.example.test/v1",
        "MODEL_API_KEY": "private-test-value",
    }


def test_real_model_retains_model_and_isolates_login_worker_database():
    args = arguments(
        [
            "--test-login",
            "--model",
            "real",
            "--settings-file",
            "private.json",
            "--executor",
            "real",
        ]
    )
    original = local_config()
    config = prepare_settings(args, original, "owned-console")
    assert original == local_config()
    assert (
        config["MODEL_NAME"] == "actual-model"
        and config["MODEL_API_KEY"] == "private-test-value"
    )
    assert config["API_BASE_URL"] == "http://model.example.test/v1"
    assert config["EW_EVENT_GROUP_NAME"] == "owned-console:ingress"
    assert (
        config["SSO_NAMESPACE"] == "owned-console:sso"
        and config["EXECUTOR_SUBMIT_ENABLED"]
    )
    runtime = public_runtime(args, load_settings(config=config, environ={}))
    assert (
        runtime["auth"]["mode"] == "fixture"
        and runtime["model"]["mode"] == "real"
    )
    assert runtime["executor"]["mode"] == "real"
    import json

    assert "private-test-value" not in json.dumps(runtime)
    assert "model.example.test" not in json.dumps(runtime)


def test_legacy_fixture_stays_fixed_and_normal_mode_retains_supplied_settings():
    args = arguments(["--local-fixtures", "--temporary-db"])
    config = prepare_settings(args, local_config(), "owned-console")
    assert args.test_login and args.model == "fixture"
    assert (
        config["MODEL_NAME"] == "contract-fixture"
        and not config["EXECUTOR_SUBMIT_ENABLED"]
    )
    normal = arguments(["--settings-file", "private.json"])
    config = local_config()
    assert prepare_settings(normal, config, "unused") == config
    assert not normal.test_login and normal.model == "configured"


@pytest.mark.parametrize(
    "flags",
    [
        [
            "--local-fixtures",
            "--temporary-db",
            "--model",
            "real",
            "--model-env",
            "private.env",
        ],
        ["--test-login", "--temporary-db", "--model", "real"],
        ["--test-login", "--temporary-db", "--model-env", "private.env"],
        ["--temporary-db", "--model", "real", "--model-env", "private.env"],
        ["--settings-file", "private.json", "--fixture-admin"],
        ["--settings-file", "private.json", "--executor", "real"],
        ["--settings-file", "private.json", "--model", "fixture"],
    ],
)
def test_conflicting_or_incomplete_modes_fail_before_start(flags):
    with pytest.raises(SystemExit) as error:
        arguments(flags)
    assert error.value.code == 2


def test_model_env_never_inherits_external_application_settings(tmp_path):
    path = tmp_path / "private.env"
    path.write_text(
        "MODEL_NAME=actual-model\nAPI_BASE_URL=http://model.example.test/v1\nMODEL_API_KEY=private-test-value\nDATABASE_URL=remote-business-db\nREDIS_URL=remote-redis\nSSO_NAMESPACE=remote-sso\n"
    )
    values = load_model_env(path)
    assert set(values) == {
        "MODEL_NAME",
        "API_BASE_URL",
        "MODEL_API_KEY",
        "MODEL_PROVIDER",
    }
    args = arguments(
        [
            "--test-login",
            "--temporary-db",
            "--model",
            "real",
            "--model-env",
            str(path),
        ]
    )
    config = prepare_settings(args, local_config(), "owned-console")
    assert config["DATABASE_URL"] == local_config()["DATABASE_URL"]
    assert config["SSO_NAMESPACE"] == "owned-console:sso"
    path.write_text(
        "MODEL_NAME=contract-fixture\nAPI_BASE_URL=http://fixture.invalid/v1\nMODEL_API_KEY=value\n"
    )
    with pytest.raises(ValueError, match="Fixture settings"):
        load_model_env(path)


def test_model_alias_is_process_scoped_and_restored_on_failure(monkeypatch):
    calls = []

    def original(host, *args, **kwargs):
        calls.append(host)

    monkeypatch.setattr(socket, "getaddrinfo", original)
    with pytest.raises(RuntimeError):
        with model_host_alias():
            socket.getaddrinfo("model.frodo.com", 80)
            socket.getaddrinfo("another.host", 80)
            raise RuntimeError("test")
    assert calls == ["10.250.110.99", "another.host"]
    assert socket.getaddrinfo is original


@pytest.mark.asyncio
async def test_real_mode_does_not_install_model_fixture(monkeypatch):
    import serve_test_console as console
    import verify_api_contract_flow as flow
    from fastapi import FastAPI

    calls = []
    args = arguments(
        ["--test-login", "--model", "real", "--settings-file", "private.json"]
    )
    monkeypatch.setattr(
        console, "migrate", lambda config: calls.append("migration")
    )
    monkeypatch.setattr(
        console,
        "install_employee_fixture",
        lambda app, employee: calls.append("employee_fixture"),
    )
    monkeypatch.setattr(console, "create_app", lambda settings: FastAPI())

    def forbidden(*args, **kwargs):
        pytest.fail("Real mode installed a model fixture")

    monkeypatch.setattr(flow, "install_model_fixture", forbidden)

    class Server:
        def __init__(self, config):
            self.app = config.app

        async def serve(self):
            import httpx
            import json
            import re

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://127.0.0.1:18100",
            ) as client:
                response = await client.get("/test-console")
                assert response.status_code == 200
                public = json.loads(
                    re.search(
                        r"window.TEST_CONSOLE_CONFIG=(.*?);</script>",
                        response.text,
                    )[1]
                )
                assert public["returnTo"] == "/test-console"
                assert public["auth"]["mode"] == "fixture"
                assert public["model"]["mode"] == "real"
                assert "private-test-value" not in response.text
            calls.append("serve")

    monkeypatch.setattr(console, "ConsoleServer", Server)

    class Redis:
        @classmethod
        def from_url(cls, url):
            return cls()

        async def xgroup_destroy(self, *args):
            calls.append("cleanup")

        async def scan_iter(self, **kwargs):
            for key in ():
                yield key

        async def aclose(self):
            pass

    import redis.asyncio

    monkeypatch.setattr(redis.asyncio, "Redis", Redis)
    await console.serve(args, local_config(), "owned-console")
    assert calls == ["migration", "employee_fixture", "serve", "cleanup"]

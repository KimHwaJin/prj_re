"""The shipped console uses the running service, without test-login overrides."""
import json
import re

from fastapi.testclient import TestClient

import dtest.settings.loader as service_settings
from dtest.bootstrap import create_app
from dtest.settings.loader import load_settings


def runtime_config(html):
    match = re.search(r"window.TEST_CONSOLE_CONFIG=(.*?);</script>", html)
    assert match
    return json.loads(match[1])


def test_demo_serves_current_console_and_keeps_api_authentication(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", None)
    settings = load_settings(config={
        "MODEL_PROVIDER": "mock", "MODEL_NAME": "demo-model",
        "MODEL_API_KEY": "private-model-key",
        "DATABASE_URL": "postgresql+asyncpg://private-user:private-password@db.example/chat_app",
        "EXECUTOR_BASE_URL": "http://executor.example",
        "EXECUTOR_SUBMIT_ENABLED": False,
        "AGENT_WORKER_ENABLED": False, "EVENT_WORKER_ENABLED": False,
        "TASK_RECONCILER_ENABLED": False,
    }, environ={})
    app = create_app(settings)
    # With background workers disabled, the public HTML requires no DB connection.
    with TestClient(app) as client:
        response = client.get("/demo")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        config = runtime_config(response.text)
        assert config == {
            "apiBase": "/api/v1", "openapiUrl": "/openapi.json", "returnTo": "/demo",
            "auth": {"mode": "configured"},
            "model": {"mode": "mock", "name": "demo-model"},
            "executor": {"mode": "off"},
        }
        assert 'id="console-app"' in response.text
        assert 'id="review"' in response.text
        assert 'id="apiOperation"' in response.text
        for private in ("private-model-key", "private-password", "private-user", "db.example", "executor.example"):
            assert private not in response.text
        assert client.get("/api/v1/users/me").status_code == 401
        assert not app.dependency_overrides
        assert client.get("/", follow_redirects=False).headers["location"] == "/demo"
    assert "/demo" not in app.openapi()["paths"]


def test_demo_uses_proxy_prefix_custom_api_and_escaped_labels(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", None)
    name = "</script><script>alert('model-label')</script>"
    settings = load_settings(config={
        "MODEL_NAME": name, "API_V1_PREFIX": "/custom/v2",
        "EXECUTOR_SUBMIT_ENABLED": True,
        "AGENT_WORKER_ENABLED": False, "EVENT_WORKER_ENABLED": False,
        "TASK_RECONCILER_ENABLED": False,
    }, environ={})
    app = create_app(settings)
    with TestClient(app, root_path="/mounted") as client:
        response = client.get("/demo")
        config = runtime_config(response.text)
        assert config["apiBase"] == "/mounted/custom/v2"
        assert config["openapiUrl"] == "/mounted/openapi.json"
        assert config["returnTo"] == "/mounted/demo"
        assert config["model"] == {"mode": "real", "name": name}
        assert config["executor"] == {"mode": "real"}
        assert name not in response.text
        assert client.get("/", follow_redirects=False).headers["location"] == "/mounted/demo"

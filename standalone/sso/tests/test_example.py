"""The supplied app starts independently and fails closed before SDK setup."""

import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI


async def test_example_startup_and_unconfigured_sdk(
    monkeypatch: pytest.MonkeyPatch,
):
    examples = Path(__file__).parent.parent / "examples"
    monkeypatch.syspath_prepend(str(examples))
    monkeypatch.setenv("SSO_CONFIG", str(examples / "config.local.json"))
    spec = importlib.util.spec_from_file_location(
        "sso_example_app", examples / "app.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.app
    assert isinstance(app, FastAPI)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:5000",
        ) as client,
    ):
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/demo")).status_code == 200
        assert (await client.get("/items")).status_code == 401
        assert (await client.get("/api/v1/auth/session")).status_code == 401
        response = await client.get(
            "/api/v1/auth/login/sso", params={"return_to": "/demo"}
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "Corporate SSO is unavailable."}
        assert "set-cookie" not in response.headers

"""Launcher loop compatibility and resource cleanup without global policies."""

import asyncio
import logging
import socket
import sys
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from dtest.bootstrap import build_server
from dtest.settings.loader import load_settings


def make_server(app: FastAPI):
    settings = load_settings(config={}, environ={})
    server = build_server(app, settings)
    server.config.port = 0
    return server


@pytest.mark.parametrize("fail", [False, True])
def test_windows_runner_closes_owned_loop_and_tasks(
    monkeypatch, caplog, fail: bool
):
    server = make_server(FastAPI())
    loops: list[asyncio.AbstractEventLoop] = []
    cleaned: list[bool] = []
    started = asyncio.Event()

    async def pending_work():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.append(True)

    async def serve(sockets=None):
        assert sockets is None
        loops.append(asyncio.get_running_loop())
        asyncio.create_task(pending_work())
        await started.wait()
        if fail:
            raise RuntimeError("startup failed")

    def forbidden(*args, **kwargs):
        raise AssertionError("must not use a global policy or Uvicorn runner")

    monkeypatch.setattr(server, "serve", serve)
    monkeypatch.setattr(uvicorn.Server, "run", forbidden)
    monkeypatch.setattr(asyncio, "set_event_loop_policy", forbidden)
    caplog.set_level(logging.INFO, logger="dtest.bootstrap")
    with monkeypatch.context() as platform:
        platform.setattr(sys, "platform", "win32")
        if fail:
            with pytest.raises(RuntimeError, match="startup failed"):
                server.run()
        else:
            server.run()

    assert isinstance(loops[0], asyncio.SelectorEventLoop)
    assert loops[0].is_closed()
    assert cleaned == [True]
    assert "service_event_loop platform=win32" in caplog.text


def test_non_windows_keeps_uvicorn_runner(monkeypatch):
    server = make_server(FastAPI())
    calls = []

    def run(instance, sockets=None):
        calls.append((instance, sockets))

    def forbidden(*args, **kwargs):
        raise AssertionError("non-Windows must keep Uvicorn's loop selection")

    monkeypatch.setattr(uvicorn.Server, "run", run)
    monkeypatch.setattr(asyncio, "Runner", forbidden)
    with socket.socket() as listener, monkeypatch.context() as platform:
        platform.setattr(sys, "platform", "linux")
        sockets = [listener]
        server.run(sockets=sockets)
        assert calls == [(server, sockets)]


def test_windows_runner_serves_http_and_closes_lifespan(monkeypatch):
    loops: list[asyncio.AbstractEventLoop] = []
    lifecycle: list[str] = []

    @asynccontextmanager
    async def lifespan(app):
        loops.append(asyncio.get_running_loop())
        lifecycle.append("started")
        try:
            yield
        finally:
            lifecycle.append("closed")

    app = FastAPI(lifespan=lifespan)

    @app.get("/probe")
    async def probe():
        assert asyncio.get_running_loop() is loops[0]
        return {"healthy": True}

    server = make_server(app)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

        async def serve(sockets=None):
            serving = asyncio.create_task(
                uvicorn.Server.serve(server, sockets=sockets)
            )
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        if serving.done():
                            await serving
                            raise AssertionError(
                                "server stopped before startup"
                            )
                        await asyncio.sleep(0.01)
                    async with httpx.AsyncClient(trust_env=False) as client:
                        response = await client.get(
                            f"http://127.0.0.1:{port}/probe"
                        )
                        assert response.status_code == 200
                        assert response.json() == {"healthy": True}
            finally:
                server.should_exit = True
                await asyncio.wait_for(serving, 5)

        monkeypatch.setattr(server, "serve", serve)
        with monkeypatch.context() as platform:
            platform.setattr(sys, "platform", "win32")
            server.run(sockets=[listener])

    assert lifecycle == ["started", "closed"]
    assert isinstance(loops[0], asyncio.SelectorEventLoop)
    assert loops[0].is_closed()

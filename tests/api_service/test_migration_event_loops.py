"""Alembic entrypoints with a real loop and isolated database doubles."""

import asyncio
import runpy
import sys
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path

import pytest
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio
from alembic import context
from alembic.config import Config

from dtest.settings import loader

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("chain", ["crud_migrations", "migrations"])
def test_alembic_executes_and_closes_its_platform_loop(
    monkeypatch, platform: str, chain: str
):
    settings = loader.load_settings(config={}, environ={})
    monkeypatch.setattr(loader, "get_settings", lambda: settings)
    monkeypatch.setattr(context, "config", Config(), raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    configured = []
    migrated = []
    loops: list[asyncio.AbstractEventLoop] = []
    disposed = []
    monkeypatch.setattr(
        context, "configure", lambda **values: configured.append(values)
    )
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(
        context, "run_migrations", lambda: migrated.append(True)
    )

    class Connection:
        def execute(self, statement):
            pass

        async def run_sync(self, callback):
            loops.append(asyncio.get_running_loop())
            if platform == "win32":
                assert isinstance(loops[-1], asyncio.SelectorEventLoop)
            callback(self)

    class Engine:
        @asynccontextmanager
        async def connect(self):
            yield Connection()

        begin = connect

        async def dispose(self):
            disposed.append(True)

    def engine(*args, **kwargs):
        return Engine()

    monkeypatch.setattr(sqlalchemy_asyncio, "async_engine_from_config", engine)
    monkeypatch.setattr(sqlalchemy_asyncio, "create_async_engine", engine)
    with monkeypatch.context() as runtime:
        runtime.setattr(sys, "platform", platform)
        runpy.run_path(str(ROOT / chain / "env.py"))

    assert migrated == [True]
    assert len(configured) == 1
    assert disposed == [True]
    assert loops[0].is_closed()
    if chain == "migrations":
        assert configured[0]["version_table"] == "ew_alembic_version"

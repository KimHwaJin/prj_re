"""LangGraph API lifespan for the Executor binding store."""

from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette

from api_service.agent_worker.api_bridge import get_api_worker_bridge


@asynccontextmanager
async def lifespan(_: Starlette):
    async with get_api_worker_bridge():
        yield


app = Starlette(lifespan=lifespan)


__all__ = ["app"]

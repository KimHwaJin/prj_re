"""Service-owned startup, usable with a local or platform-created FastAPI app."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import json
import logging
import sys
from pathlib import Path
from typing import Callable, Awaitable, Any
from uuid import uuid4

from fastapi import Request

# Root app.py and src/app coexist during migration. Prefer the package.
_SRC = str(Path(__file__).resolve().parent)
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

from service_settings import ServiceSettings, configure, get_settings, load_settings

log = logging.getLogger(__name__)


class BackgroundRuntime:
    """Owns background loops once per application lifespan, not per router."""

    def __init__(self, factories: dict[str, Callable[[], Awaitable[Any]]], timeout: float, *,
                 stop_event: asyncio.Event | None = None, drain_timeout: float = 0):
        self.factories = factories
        self.timeout = timeout
        self.stop_event = stop_event if stop_event is not None else asyncio.Event()
        self.drain_timeout = drain_timeout
        self.draining = False
        self._cleanup_deadline: float | None = None
        self._drain_deadline: float | None = None
        self._stop_task: asyncio.Task | None = None
        self.tasks: dict[str, asyncio.Task] = {}
        self.started = False

    @property
    def ready(self) -> bool:
        from app.core.execution_lifecycle import execution_health
        return self.started and not self.draining and execution_health.healthy and all(not task.done() for task in self.tasks.values())

    def _observe(self, task: asyncio.Task) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                # Exceptions can contain external payloads or connection URLs.
                log.error("background_loop_failed name=%s error_type=%s", task.get_name(), type(error).__name__)
            elif self.started:
                log.error("background_loop_exited name=%s", task.get_name())

    async def start(self) -> None:
        if self.started or self.tasks or self.draining:
            raise RuntimeError("Background runtime has already started")
        try:
            for name, factory in self.factories.items():
                task = asyncio.create_task(factory(), name=name)
                self.tasks[name] = task
                task.add_done_callback(self._observe)
            self.started = True
            await asyncio.sleep(0)
            if not self.ready:
                raise RuntimeError("A background loop exited during startup")
        except BaseException:
            await self.stop(graceful=False)
            raise

    def request_stop(self, *, graceful: bool = True) -> None:
        """Start one shutdown clock, including when called by the server signal hook."""
        now = asyncio.get_running_loop().time()
        if not self.draining:
            self.draining = True
            self._drain_deadline = now + (self.drain_timeout if graceful else 0)
            log.info("service_draining grace_seconds=%s", self.drain_timeout if graceful else 0)
        elif not graceful:
            self._drain_deadline = now
        self.started = False
        self.stop_event.set()
        if self._stop_task is None or (self._stop_task.done() and
                (self._stop_task.cancelled() or self._stop_task.exception() is not None)):
            self._stop_task = asyncio.create_task(self._stop(), name="service-owned-shutdown")
            self._stop_task.add_done_callback(self._observe)

    async def stop(self, *, graceful: bool = True) -> None:
        from app.core.execution_lifecycle import protected_cleanup
        self.request_stop(graceful=graceful)
        task = self._stop_task
        async def join():
            await asyncio.shield(task)
        await protected_cleanup(join())

    async def _stop(self) -> None:
        pending = [task for task in self.tasks.values() if not task.done()]
        if pending:
            remaining = max(0, self._drain_deadline - asyncio.get_running_loop().time())
            _, pending = await asyncio.wait(pending, timeout=remaining)
        if self._cleanup_deadline is None:
            self._cleanup_deadline = asyncio.get_running_loop().time() + self.timeout
        for task in pending:
            if not task.cancelling():
                task.cancel()
        if pending:
            log.info("service_drain_expired cancelling_workers=%s", len(pending))
            _, unfinished = await asyncio.wait(pending, timeout=max(0, self._cleanup_deadline - asyncio.get_running_loop().time()))
            if unfinished:
                names = ", ".join(sorted(task.get_name() for task in unfinished))
                raise RuntimeError(f"Background shutdown deadline exceeded: {names}")
        # Retrieve errors without replacing the original request/startup error.
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        from app.core.execution_lifecycle import execution_health
        recorders = list(execution_health.recorders)
        if recorders:
            _, unfinished = await asyncio.wait(recorders, timeout=max(0, self._cleanup_deadline - asyncio.get_running_loop().time()))
            if unfinished:
                raise RuntimeError("Recovery recording shutdown deadline exceeded")
            await asyncio.gather(*recorders, return_exceptions=True)
        self.tasks.clear()


def _background_factories(settings: ServiceSettings, stop_event: asyncio.Event) -> dict[str, Callable]:
    factories: dict[str, Callable] = {}
    if settings.api.task_reconciler_enabled:
        from app.task_lock_reconciler import run_forever as reconcile
        factories["task-lock-reconciler"] = lambda: reconcile(stop_event=stop_event)
    if settings.api.agent_worker_enabled:
        from app.agent_run_worker import run_forever
        factories["agent-run-worker"] = lambda: run_forever(stop_event=stop_event)
    if settings.event_worker_enabled:
        from app.agent_worker.worker_main import main
        # The embedding app owns signals; the standalone entrypoint owns its own.
        factories["executor-event-worker"] = lambda: main(install_signals=False, stop_event=stop_event)
    return factories


async def _close_resources() -> None:
    from app.services.agent_graph_service import GraphResourcesBusy, runtime
    from app.core.database import close_database
    from app.agent_worker.api_bridge import close_api_worker_bridge
    # A live borrower still uses CRUD/bridge resources too. Preserve all of them
    # if draining failed; ordinary close errors still run remaining cleanups.
    try:
        await runtime.shutdown()
    except (GraphResourcesBusy, asyncio.CancelledError):
        raise
    except BaseException:
        async with AsyncExitStack() as stack:
            stack.push_async_callback(close_database)
            stack.push_async_callback(close_api_worker_bridge)
        raise
    async with AsyncExitStack() as stack:
        stack.push_async_callback(close_database)
        stack.push_async_callback(close_api_worker_bridge)


def attach_service(
    app,
    settings: ServiceSettings,
    *,
    router=None,
    background_factories: dict[str, Callable] | None = None,
    close_resources: Callable[[], Awaitable[None]] = _close_resources,
):
    """Attach once; preserve the app's existing and included-router lifespans.

    The platform must finish assigning its lifespan before calling this function.
    Calling a platform main() that overwrites lifespan afterwards is unsupported.
    """
    if getattr(app.state, "dtest_attached", False):
        raise RuntimeError("Service is already attached to this app")
    configure(settings)
    if router is None:
        from app.api.v1.router import api_router
        router = api_router
    app.include_router(router, prefix=settings.api.api_v1_prefix)
    previous_lifespan = app.router.lifespan_context
    stop_event = asyncio.Event()
    background = BackgroundRuntime(
        _background_factories(settings, stop_event) if background_factories is None else background_factories,
        settings.shutdown_timeout_seconds, stop_event=stop_event,
        drain_timeout=settings.shutdown_drain_seconds,
    )

    @asynccontextmanager
    async def combined_lifespan(application):
        # Lifespan state returned by the platform is preserved for requests.
        async with previous_lifespan(application) as state:
            from app.services.agent_graph_service import runtime
            runtime.start()
            try:
                await background.start()
                log.info("service_started profile=%s", settings.profile)
                yield state
            finally:
                # A timed-out background task may still be using these pools.
                # Leave them owned until process termination rather than closing
                # resources beneath a live graph.
                from app.core.execution_lifecycle import protected_cleanup
                async def shutdown():
                    await background.stop()
                    await close_resources()
                await protected_cleanup(shutdown())

    app.router.lifespan_context = combined_lifespan
    app.state.dtest_attached = True
    app.state.service_settings = settings
    app.state.service_runtime = background
    return app


def create_app(settings: ServiceSettings | None = None, *, platform_app=None):
    """Local factory or explicit attachment to an already assembled platform app."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import FileResponse, RedirectResponse, JSONResponse

    settings = settings or get_settings()
    configure(settings)
    from app.core.problems import http_exception_handler, unhandled_exception_handler, validation_exception_handler

    app = platform_app if platform_app is not None else FastAPI(title=settings.api.app_name, version="1.0.0")
    attach_service(app, settings)
    # Service handlers require request_id. Preserve one from platform middleware.
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        if not getattr(request.state, "request_id", None):
            request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    if platform_app is None:
        app.add_exception_handler(HTTPException, http_exception_handler)
        app.add_exception_handler(RequestValidationError, validation_exception_handler)
        app.add_exception_handler(Exception, unhandled_exception_handler)

        @app.get("/health", tags=["health"])
        async def health():
            return {"status": "ok"}

        @app.get("/", include_in_schema=False)
        async def root():
            return RedirectResponse(url="/demo")

        @app.get("/demo", include_in_schema=False)
        async def demo():
            return FileResponse(Path(__file__).parent / "app/static/demo.html")

    @app.get("/service/ready", tags=["health"])
    async def ready():
        healthy = app.state.service_runtime.ready
        return JSONResponse({"ready": healthy}, status_code=200 if healthy else 503)

    @app.get("/service/live", tags=["health"])
    async def live():
        from app.core.execution_lifecycle import execution_health
        healthy = execution_health.healthy
        return JSONResponse({"healthy": healthy}, status_code=200 if healthy else 503)

    return app


def build_server(app, settings: ServiceSettings):
    """Root launcher: notify Workers at SIGTERM, before HTTP connection drain."""
    import uvicorn

    class DrainServer(uvicorn.Server):
        def handle_exit(self, sig, frame):
            app.state.service_runtime.request_stop()
            super().handle_exit(sig, frame)

    return DrainServer(uvicorn.Config(
        app, host=settings.api.server_host, port=settings.api.server_port,
        log_config=None, timeout_graceful_shutdown=settings.shutdown_timeout_seconds,
    ))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Start the DTest API and configured Workers")
    parser.add_argument("--env", choices=("dev", "stg", "prd"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--local-env-file", type=Path)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    settings = configure(load_settings(profile=args.env, config_path=args.config, dotenv_path=args.local_env_file))
    if args.check_config:
        print(json.dumps(settings.summary(), ensure_ascii=False, indent=2))
        return
    if settings.api.server_reload:
        parser.error("server_reload is not supported by this single-process bootstrap")
    logging.basicConfig(level=logging.INFO)
    build_server(create_app(settings), settings).run()

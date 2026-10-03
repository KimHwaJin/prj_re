"""Service-owned startup, usable with a local or platform-created FastAPI app."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import json
import logging
from pathlib import Path
from typing import Callable, Awaitable, Any
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
        from api_service.core.execution_lifecycle import execution_health
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
        from service_runtime.cleanup import protected_cleanup
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
        from api_service.core.execution_lifecycle import execution_health
        recorders = list(execution_health.recorders)
        if recorders:
            _, unfinished = await asyncio.wait(recorders, timeout=max(0, self._cleanup_deadline - asyncio.get_running_loop().time()))
            if unfinished:
                raise RuntimeError("Recovery recording shutdown deadline exceeded")
            await asyncio.gather(*recorders, return_exceptions=True)
        self.tasks.clear()


def _background_factories(settings: ServiceSettings, stop_event: asyncio.Event, *, on_event_worker=None) -> dict[str, Callable]:
    factories: dict[str, Callable] = {}
    if settings.api.task_reconciler_enabled:
        from api_service.task_lock_reconciler import run_forever as reconcile
        factories["task-lock-reconciler"] = lambda: reconcile(stop_event=stop_event)
    if settings.api.agent_worker_enabled:
        from api_service.agent_run_worker import run_forever
        factories["agent-run-worker"] = lambda: run_forever(stop_event=stop_event)
    if settings.event_worker_enabled:
        from api_service.agent_worker.worker_main import main
        # The embedding app owns signals; the standalone entrypoint owns its own.
        factories["executor-event-worker"] = lambda: main(install_signals=False, stop_event=stop_event, use_shared_graph=True, on_worker=on_event_worker)
    return factories


async def _close_resources() -> None:
    from api_service.services.agent_graph_service import GraphResourcesBusy, runtime
    from api_service.core.database import close_database
    from api_service.core.memory_store import runtime as memory_store_runtime
    from api_service.agent_worker.api_bridge import close_api_worker_bridge
    # A live borrower still uses CRUD/bridge resources too. Preserve all of them
    # if draining failed; ordinary close errors still run remaining cleanups.
    try:
        await runtime.shutdown()
    except (GraphResourcesBusy, asyncio.CancelledError):
        raise
    except BaseException:
        async with AsyncExitStack() as stack:
            stack.push_async_callback(close_database)
            stack.push_async_callback(memory_store_runtime.shutdown)
            stack.push_async_callback(close_api_worker_bridge)
        raise
    async with AsyncExitStack() as stack:
        stack.push_async_callback(close_database)
        stack.push_async_callback(memory_store_runtime.shutdown)
        stack.push_async_callback(close_api_worker_bridge)


def attach_service(
    app,
    settings: ServiceSettings,
    *,
    router=None,
    background_factories: dict[str, Callable] | None = None,
    close_resources: Callable[[], Awaitable[None]] = _close_resources,
    sso_docs_path: str = "/service/docs",
):
    """Attach once; preserve the app's existing and included-router lifespans.

    The platform must finish assigning its lifespan before calling this function.
    Calling a platform main() that overwrites lifespan afterwards is unsupported.
    """
    if getattr(app.state, "dtest_attached", False):
        raise RuntimeError("Service is already attached to this app")
    configure(settings)
    if router is None:
        from api_service.api.v1.router import api_router
        router = api_router
    app.include_router(router, prefix=settings.api.api_v1_prefix)
    from service_auth.sso.runtime import attach_sso
    from service_auth.sso.swagger import attach_swagger
    from api_service.services.sso_user_service import SsoUserDirectory
    sso = attach_sso(app, settings=settings.sso,
        users=SsoUserDirectory(auto_register=settings.sso.auto_register),
        redis_url=settings.api.redis_url, api_prefix=settings.api.api_v1_prefix, docs_path=sso_docs_path)
    attach_swagger(app, sso, docs_path=sso_docs_path)
    previous_lifespan = app.router.lifespan_context
    stop_event = asyncio.Event()
    app.state.executor_event_worker = None
    def publish_event_worker(worker):
        app.state.executor_event_worker = worker

    background = BackgroundRuntime(
        _background_factories(settings, stop_event, on_event_worker=publish_event_worker) if background_factories is None else background_factories,
        settings.shutdown_timeout_seconds, stop_event=stop_event,
        drain_timeout=settings.shutdown_drain_seconds,
    )

    from api_service.services.run_stream_service import RunStreamHub
    stream_hub = RunStreamHub(settings.api)
    app.state.run_stream_hub = stream_hub

    @asynccontextmanager
    async def combined_lifespan(application):
        # Lifespan state returned by the platform is preserved for requests.
        async with previous_lifespan(application) as state:
            from api_service.services.agent_graph_service import runtime
            runtime.start()
            from api_service.core.memory_store import runtime as memory_store_runtime
            memory_store_runtime.start()
            try:
                from api_service.observability.phoenix import setup_phoenix
                await asyncio.to_thread(setup_phoenix, settings.agent)
                await background.start()
                log.info("service_started profile=%s", settings.profile)
                yield state
            finally:
                # A timed-out background task may still be using these pools.
                # Leave them owned until process termination rather than closing
                # resources beneath a live graph.
                from service_runtime.cleanup import protected_cleanup
                async def shutdown():
                    try:
                        await stream_hub.close()
                        await background.stop()
                        await close_resources()
                        from api_service.observability.phoenix import shutdown_phoenix
                        await asyncio.to_thread(shutdown_phoenix)
                    finally:
                        await sso.close()
                await protected_cleanup(shutdown())

    app.router.lifespan_context = combined_lifespan
    app.state.dtest_attached = True
    app.state.service_settings = settings
    app.state.service_runtime = background
    return app


def create_app(settings: ServiceSettings | None = None, *, platform_app=None):
    """Local factory or explicit attachment to an already assembled platform app."""
    from fastapi import FastAPI, HTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import FileResponse, RedirectResponse, JSONResponse

    settings = settings or get_settings()
    configure(settings)
    from api_service.core.problems import http_exception_handler, unhandled_exception_handler, validation_exception_handler

    app = platform_app if platform_app is not None else FastAPI(title=settings.api.app_name, version="1.0.0", docs_url=None)
    attach_service(app, settings, sso_docs_path="/docs" if platform_app is None else "/service/docs")
    # Preserve a platform ID; avoid introducing stream cancellation scopes.
    from service_runtime.request_id import RequestIdMiddleware
    app.add_middleware(RequestIdMiddleware)

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
            return FileResponse(Path(__file__).parent / "api_service/static/demo.html")

    @app.get("/service/ready", tags=["health"])
    async def ready():
        healthy = app.state.service_runtime.ready
        checks = []
        if healthy and settings.event_worker_enabled:
            worker = app.state.executor_event_worker
            if worker is None:
                healthy = False
            else:
                checks.append(worker.ready)
        if healthy and (settings.api.agent_worker_enabled or settings.api.task_reconciler_enabled):
            async def primary_database_ready():
                # Legacy Event DB overrides do not prove API queue readiness.
                from api_service.core.database import short_session
                from sqlalchemy import text
                try:
                    async with asyncio.timeout(2):
                        async with short_session() as db:
                            await db.execute(text("SELECT 1 FROM tasks LIMIT 0"))
                    return True
                except Exception:
                    return False
            checks.append(primary_database_ready)
        if healthy and checks:
            results = await asyncio.gather(*(check() for check in checks), return_exceptions=True)
            healthy = all(result is True for result in results)
        return JSONResponse({"ready": healthy}, status_code=200 if healthy else 503)

    @app.get("/service/metrics", include_in_schema=False)
    async def metrics():
        # Retain Event Worker telemetry without a second HTTP listener. The
        # default registry also carries process/platform metrics when registered.
        from fastapi.responses import Response
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        payload = generate_latest()
        worker = app.state.executor_event_worker
        if worker is not None:
            payload += generate_latest(worker.telemetry.registry)
        return Response(payload, headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.get("/service/live", tags=["health"])
    async def live():
        from api_service.core.execution_lifecycle import execution_health
        healthy = execution_health.healthy and all(
            not task.done() for task in app.state.service_runtime.tasks.values()
        )
        return JSONResponse({"healthy": healthy}, status_code=200 if healthy else 503)

    return app


def build_server(app, settings: ServiceSettings):
    """Root launcher: notify Workers at SIGTERM, before HTTP connection drain."""
    import uvicorn

    class DrainServer(uvicorn.Server):
        def handle_exit(self, sig, frame):
            app.state.run_stream_hub.begin_shutdown()
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
    logging.basicConfig(level=logging.INFO)
    settings = configure(load_settings(profile=args.env, config_path=args.config, dotenv_path=args.local_env_file))
    if settings.api.server_reload:
        parser.error("server_reload is not supported by this single-process bootstrap")
    if args.check_config:
        print(json.dumps(settings.summary(), ensure_ascii=False, indent=2))
        return
    build_server(create_app(settings), settings).run()

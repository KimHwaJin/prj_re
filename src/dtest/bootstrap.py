"""Service-owned startup, usable with a local or platform-created FastAPI app."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.requests import Request

from dtest.settings.loader import (
    ServiceSettings,
    configure,
    get_settings,
    load_settings,
)

log = logging.getLogger(__name__)


class BackgroundRuntime:
    """Owns background loops once per application lifespan, not per router."""

    def __init__(
        self,
        factories: dict[str, Callable[[], Awaitable[Any]]],
        timeout: float,
        *,
        stop_event: asyncio.Event | None = None,
        drain_timeout: float = 0,
    ):
        self.factories = factories
        self.timeout = timeout
        self.stop_event = (
            stop_event if stop_event is not None else asyncio.Event()
        )
        self.drain_timeout = drain_timeout
        self.draining = False
        self._cleanup_deadline: float | None = None
        self._drain_deadline: float | None = None
        self._stop_task: asyncio.Task | None = None
        self.tasks: dict[str, asyncio.Task] = {}
        self.started = False

    @property
    def ready(self) -> bool:
        from dtest.application.runs.lifecycle import execution_health

        return (
            self.started
            and not self.draining
            and execution_health.healthy
            and all(not task.done() for task in self.tasks.values())
        )

    def _observe(self, task: asyncio.Task) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                # Exceptions can contain external payloads or connection URLs.
                log.error(
                    "background_loop_failed name=%s error_type=%s",
                    task.get_name(),
                    type(error).__name__,
                )
                from dtest.infrastructure.database.failures import (
                    database_failure,
                )

                failure = database_failure(error)
                if failure is not None:
                    log.error(
                        "background_database_failure name=%s cause_type=%s "
                        "sqlstate=%s recovery=%s",
                        task.get_name(),
                        failure.cause_type,
                        failure.sqlstate or "unknown",
                        failure.recovery,
                    )
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
            self._drain_deadline = now + (
                self.drain_timeout if graceful else 0
            )
            log.info(
                "service_draining grace_seconds=%s",
                self.drain_timeout if graceful else 0,
            )
        elif not graceful:
            self._drain_deadline = now
        self.started = False
        self.stop_event.set()
        if self._stop_task is None or (
            self._stop_task.done()
            and (
                self._stop_task.cancelled()
                or self._stop_task.exception() is not None
            )
        ):
            self._stop_task = asyncio.create_task(
                self._stop(), name="service-owned-shutdown"
            )
            self._stop_task.add_done_callback(self._observe)

    async def stop(self, *, graceful: bool = True) -> None:
        from dtest.lifecycle import protected_cleanup

        self.request_stop(graceful=graceful)
        task = self._stop_task

        async def join():
            await asyncio.shield(task)

        await protected_cleanup(join())

    async def _stop(self) -> None:
        pending = [task for task in self.tasks.values() if not task.done()]
        if pending:
            remaining = max(
                0, self._drain_deadline - asyncio.get_running_loop().time()
            )
            _, pending = await asyncio.wait(pending, timeout=remaining)
        if self._cleanup_deadline is None:
            self._cleanup_deadline = (
                asyncio.get_running_loop().time() + self.timeout
            )
        for task in pending:
            if not task.cancelling():
                task.cancel()
        if pending:
            log.info(
                "service_drain_expired cancelling_workers=%s", len(pending)
            )
            _, unfinished = await asyncio.wait(
                pending,
                timeout=max(
                    0,
                    self._cleanup_deadline - asyncio.get_running_loop().time(),
                ),
            )
            if unfinished:
                names = ", ".join(
                    sorted(task.get_name() for task in unfinished)
                )
                raise RuntimeError(
                    f"Background shutdown deadline exceeded: {names}"
                )
        # Retrieve errors without replacing the original request/startup error.
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        from dtest.application.runs.lifecycle import execution_health

        recorders = list(execution_health.recorders)
        if recorders:
            _, unfinished = await asyncio.wait(
                recorders,
                timeout=max(
                    0,
                    self._cleanup_deadline - asyncio.get_running_loop().time(),
                ),
            )
            if unfinished:
                raise RuntimeError(
                    "Recovery recording shutdown deadline exceeded"
                )
            await asyncio.gather(*recorders, return_exceptions=True)
        self.tasks.clear()


def _background_factories(
    settings: ServiceSettings,
    stop_event: asyncio.Event,
    *,
    on_event_worker=None,
) -> dict[str, Callable]:
    factories: dict[str, Callable] = {}
    if settings.commands.task_reconciler_enabled:
        from dtest.worker_service.reconciler import run_forever as reconcile

        factories["task-lock-reconciler"] = lambda: reconcile(
            stop_event=stop_event
        )
    if settings.commands.agent_worker_enabled or settings.event_worker_enabled:
        from dtest.worker_service.command_worker import run_forever

        factories["agent-run-worker"] = lambda: run_forever(
            stop_event=stop_event
        )
    if settings.event_worker_enabled:
        from dtest.worker_service.executor_events.main import main

        # The embedding app owns signals; the standalone entrypoint owns its own.
        factories["executor-event-worker"] = lambda: main(
            install_signals=False,
            stop_event=stop_event,
            on_worker=on_event_worker,
        )
    return factories


async def _close_resources() -> None:
    from dtest.application.runs.runtime import GraphResourcesBusy, runtime
    from dtest.infrastructure.database.runtime import close_database
    from dtest.infrastructure.memory.store import (
        runtime as memory_store_runtime,
    )
    from dtest.infrastructure.workflow_search.runtime import (
        close_workflow_runtime,
    )

    # A live borrower still uses CRUD/bridge resources too. Preserve all of them
    # if draining failed; ordinary close errors still run remaining cleanups.
    try:
        await runtime.shutdown()
    except (GraphResourcesBusy, asyncio.CancelledError):
        raise
    except BaseException:
        async with AsyncExitStack() as stack:
            stack.push_async_callback(close_database)
            stack.push_async_callback(close_workflow_runtime)
            stack.push_async_callback(memory_store_runtime.shutdown)
        raise
    async with AsyncExitStack() as stack:
        stack.push_async_callback(close_database)
        stack.push_async_callback(close_workflow_runtime)
        stack.push_async_callback(memory_store_runtime.shutdown)


def attach_service(
    app,
    settings: ServiceSettings,
    *,
    router=None,
    background_factories: dict[str, Callable] | None = None,
    close_resources: Callable[[], Awaitable[None]] = _close_resources,
    sso_docs_path: str = "/service/docs",
    manage_tracing: bool = False,
):
    """Attach once; preserve the app's existing and included-router lifespans.

    The platform must finish assigning its lifespan before calling this function.
    Calling a platform main() that overwrites lifespan afterwards is unsupported.
    """
    if getattr(app.state, "dtest_attached", False):
        raise RuntimeError("Service is already attached to this app")
    configure(settings)
    from dtest.container import install_container

    install_container()
    if router is None:
        from dtest.api_service.http.v1.router import api_router

        router = api_router
    app.include_router(router, prefix=settings.api.api_v1_prefix)
    from dtest.api_service.auth.runtime import attach_sso
    from dtest.api_service.auth.swagger import attach_swagger
    from dtest.application.resources.sso_users import SsoUserDirectory

    sso = attach_sso(
        app,
        settings=settings.sso,
        users=SsoUserDirectory(auto_register=settings.sso.auto_register),
        redis_url=settings.redis.redis_url,
        api_prefix=settings.api.api_v1_prefix,
        docs_path=sso_docs_path,
    )
    attach_swagger(app, sso, docs_path=sso_docs_path)
    previous_lifespan = app.router.lifespan_context
    stop_event = asyncio.Event()
    app.state.executor_event_worker = None

    def publish_event_worker(worker):
        app.state.executor_event_worker = worker

    background = BackgroundRuntime(
        _background_factories(
            settings, stop_event, on_event_worker=publish_event_worker
        )
        if background_factories is None
        else background_factories,
        settings.shutdown_timeout_seconds,
        stop_event=stop_event,
        drain_timeout=settings.shutdown_drain_seconds,
    )

    from dtest.api_service.streaming import RunStreamHub

    stream_hub = RunStreamHub(settings.api)
    app.state.run_stream_hub = stream_hub

    @asynccontextmanager
    async def combined_lifespan(application):
        if settings.db_init_on_start:
            from dtest.infrastructure.database.schema import initialize_schema
            from dtest.lifecycle import protected_cleanup

            try:
                await initialize_schema(settings)
            except BaseException:
                await protected_cleanup(sso.close())
                raise
        # Lifespan state returned by the platform is preserved for requests.
        async with previous_lifespan(application) as state:
            from dtest.application.runs.runtime import runtime

            runtime.start()
            from dtest.infrastructure.memory.store import (
                runtime as memory_store_runtime,
            )

            memory_store_runtime.start()
            try:
                if manage_tracing:
                    from dtest.infrastructure.observability.phoenix import (
                        setup_phoenix,
                    )

                    await asyncio.to_thread(setup_phoenix, settings.agent)
                await background.start()
                log.info("service_started profile=%s", settings.profile)
                yield state
            finally:
                # A timed-out background task may still be using these pools.
                # Leave them owned until process termination rather than closing
                # resources beneath a live graph.
                from dtest.lifecycle import protected_cleanup

                async def shutdown():
                    try:
                        await stream_hub.close()
                        await background.stop()
                        await close_resources()
                        if manage_tracing:
                            from dtest.infrastructure.observability.phoenix import (
                                shutdown_phoenix,
                            )

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
    from fastapi.responses import JSONResponse, RedirectResponse

    settings = settings or get_settings()
    configure(settings)
    from dtest.container import install_container

    install_container()
    from dtest.api_service.http.problems import (
        http_exception_handler,
        unhandled_exception_handler,
        validation_exception_handler,
    )

    app = (
        platform_app
        if platform_app is not None
        else FastAPI(
            title=settings.api.app_name, version="1.0.0", docs_url=None
        )
    )
    attach_service(
        app,
        settings,
        sso_docs_path="/docs" if platform_app is None else "/service/docs",
        manage_tracing=platform_app is None,
    )
    # Preserve a platform ID; avoid introducing stream cancellation scopes.
    from dtest.api_service.middleware.request_id import RequestIdMiddleware

    app.add_middleware(RequestIdMiddleware)
    from dtest.api_service.http.problems import run_exception_handler
    from dtest.application.runs.errors import RunError

    app.add_exception_handler(RunError, run_exception_handler)
    from dtest.api_service.http.problems import application_exception_handler
    from dtest.contracts.errors import ApplicationError

    app.add_exception_handler(ApplicationError, application_exception_handler)

    if platform_app is None:
        app.add_exception_handler(HTTPException, http_exception_handler)
        app.add_exception_handler(
            RequestValidationError, validation_exception_handler
        )
        app.add_exception_handler(Exception, unhandled_exception_handler)

        @app.get("/health", tags=["health"])
        async def health():
            return {"status": "ok"}

        @app.get("/", include_in_schema=False)
        async def root(request: Request):
            return RedirectResponse(
                url=request.scope.get("root_path", "").rstrip("/") + "/demo"
            )

        @app.get("/demo", include_in_schema=False)
        async def demo(request: Request):
            from dtest.api_service.web.console import render_console

            prefix = request.scope.get("root_path", "").rstrip("/")
            # Public labels and paths only. Never inject model/DB/SSO credentials.
            return await render_console(
                {
                    "apiBase": prefix + settings.api.api_v1_prefix,
                    "openapiUrl": prefix + app.openapi_url,
                    "returnTo": prefix + "/demo",
                    "auth": {"mode": "configured"},
                    "model": {
                        "mode": "mock"
                        if settings.agent.model_provider == "mock"
                        else "real",
                        "name": settings.agent.model_name,
                    },
                    "executor": {
                        "mode": "real"
                        if settings.agent.executor_submit_enabled
                        else "off"
                    },
                }
            )

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
        if healthy and (
            settings.commands.agent_worker_enabled
            or settings.event_worker_enabled
            or settings.commands.task_reconciler_enabled
        ):

            async def primary_database_ready():
                # Legacy Event DB overrides do not prove API queue readiness.
                from sqlalchemy import text

                from dtest.infrastructure.database.runtime import short_session

                try:
                    async with asyncio.timeout(2):
                        async with short_session() as db:
                            await db.execute(
                                text("SELECT 1 FROM tasks LIMIT 0")
                            )
                            complete = await db.scalar(
                                text("""SELECT NOT EXISTS (
                                SELECT 1 FROM agent_runs r WHERE r.status IN ('pending','running')
                                AND NOT EXISTS (SELECT 1 FROM agent_commands c
                                    WHERE c.namespace=:namespace AND c.invocation_id=r.run_id))"""),
                                {"namespace": settings.worker.namespace},
                            )
                            if not complete:
                                return False
                    return True
                except Exception:
                    return False

            checks.append(primary_database_ready)
        if healthy and checks:
            results = await asyncio.gather(
                *(check() for check in checks), return_exceptions=True
            )
            healthy = all(result is True for result in results)
        return JSONResponse(
            {"ready": healthy}, status_code=200 if healthy else 503
        )

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
        from dtest.application.runs.lifecycle import execution_health

        healthy = execution_health.healthy and all(
            not task.done()
            for task in app.state.service_runtime.tasks.values()
        )
        return JSONResponse(
            {"healthy": healthy}, status_code=200 if healthy else 503
        )

    return app


def build_server(app, settings: ServiceSettings):
    """Root launcher: notify Workers at SIGTERM, before HTTP connection drain."""
    import socket
    import sys

    import uvicorn

    class DrainServer(uvicorn.Server):
        def run(self, sockets: list[socket.socket] | None = None) -> None:
            if sys.platform != "win32":
                super().run(sockets=sockets)
                return

            # Psycopg async pools require Selector on Windows. Uvicorn may
            # supply its own Proactor factory, ignoring a global loop policy.
            # Own the loop here; Runner also closes tasks and async generators.
            with asyncio.Runner(
                loop_factory=asyncio.SelectorEventLoop
            ) as runner:
                log.info(
                    "service_event_loop platform=win32 implementation=%s",
                    type(runner.get_loop()).__name__,
                )
                runner.run(self.serve(sockets=sockets))

        def handle_exit(self, sig, frame):
            app.state.run_stream_hub.begin_shutdown()
            app.state.service_runtime.request_stop()
            super().handle_exit(sig, frame)

    return DrainServer(
        uvicorn.Config(
            app,
            host=settings.api.server_host,
            port=settings.api.server_port,
            log_config=None,
            timeout_graceful_shutdown=settings.shutdown_timeout_seconds,
        )
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Start the DTest API and configured Workers"
    )
    parser.add_argument("--env", choices=("local", "dev", "stg", "prd"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--local-env-file", type=Path)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = configure(
        load_settings(
            profile=args.env,
            config_path=args.config,
            dotenv_path=args.local_env_file,
        )
    )
    if settings.api.server_reload:
        parser.error(
            "server_reload is not supported by this single-process bootstrap"
        )
    if args.check_config:
        print(json.dumps(settings.summary(), ensure_ascii=False, indent=2))
        return
    build_server(create_app(settings), settings).run()

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from typing import Self

import httpx
from prometheus_client import generate_latest
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from service_runtime.cleanup import protected_cleanup
from api_service.worker.config import Settings
from api_service.worker.consumer import (
    RedisStreamConsumer,
    RedisStreamConsumerConfig,
    AckDecision,
    StreamMessageHandler,
)
from api_service.worker.ingress import EventRouter, Ingress
from api_service.worker.redis_streams import group_progress
from api_service.worker.store import Store
from api_service.worker.telemetry import Telemetry
from api_service.worker.wakeup import binding_subscription

logger = logging.getLogger(__name__)


class _WakeAfterCommit:
    """Coalesce a local wake only after the durable handler succeeds.

    The wrapped consumer still owns ACK/retry/lease semantics. These in-memory
    hints carry no business data; periodic DB scans remain authoritative.
    """
    def __init__(self, handler: StreamMessageHandler, wake: asyncio.Event, *,
                 event_types: set[str] | None = None) -> None:
        self.handler, self.wake, self.event_types = handler, wake, event_types

    def lock_key(self, message):
        return self.handler.lock_key(message)

    async def handle(self, message):
        result = await self.handler.handle(message)
        if (result.decision == AckDecision.ACK and
                (self.event_types is None or message.fields.get('event_type') in self.event_types)):
            self.wake.set()
        return result


class ExecutorWorker:
    def __init__(
        self,
        settings: Settings,
        event_types: set[str] | frozenset[str],
    ) -> None:
        self._router_wake = asyncio.Event()
        self.settings = settings
        self.event_types = set(event_types)
        self.pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.pool_size,
            open=False,
        )
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.request_timeout_seconds,
            socket_timeout=max(10, settings.request_timeout_seconds),
        )
        self.http = httpx.AsyncClient(
            base_url=settings.executor_base_url.rstrip("/") + "/",
            timeout=settings.request_timeout_seconds,
        )
        self.store = Store(self.pool, settings.namespace)
        self.bindings = self.store
        self.telemetry = Telemetry()
        self.router = EventRouter(
            self.store,
            self.http,
            self.event_types,
            batch_size=settings.batch_size,
            concurrency=settings.ingress_workers,
        )
        self.ingress = Ingress(self.store)
        self.consumers = [
            self._consumer(
                "ingress",
                settings.executor_event_stream,
                settings.event_group,
                lambda _: _WakeAfterCommit(self.ingress, self._router_wake, event_types=self.event_types),
                settings.ingress_workers,
            ),
        ]
        self._readiness_checks: dict[str, Callable[[], Awaitable[bool]]] = {}
        self._stop = asyncio.Event()
        self._running = False
        self._stack = AsyncExitStack()

    def _consumer(self, kind, stream, group, factory, concurrency):
        settings = self.settings
        return RedisStreamConsumer(
            self.redis,
            RedisStreamConsumerConfig(
                stream=stream,
                group=group,
                consumer_prefix=f"{settings.instance_id}-{kind}",
                concurrency=concurrency,
                block_milliseconds=1000,
                claim_idle_milliseconds=settings.claim_idle_milliseconds,
                claim_batch_size=settings.batch_size,
                dead_letter_stream=f"{settings.namespace}:{kind}:dlq",
                lock_ttl_seconds=settings.lease_ttl_seconds,
                lock_renew_interval_seconds=settings.lease_renew_seconds,
                # Redis 6.0 idle measures successful work, not liveness.
                # Do not delete another replica's idle consumer on startup.
                consumer_gc_idle_milliseconds=None,
                retry_state_ttl_seconds=604800,
                retry_key_prefix=f"{settings.namespace}:transport-retries",
            ),
            factory,
            observer=self.telemetry.observer(kind),
        )

    async def __aenter__(self) -> Self:
        try:
            self._stack.push_async_callback(self.redis.aclose)
            self._stack.push_async_callback(self.http.aclose)
            self._stack.push_async_callback(self.pool.close)
            await self.pool.open(wait=True)
            self._stack.enter_context(binding_subscription(
                self.pool.conninfo, self.settings.namespace, self._router_wake
            ))
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *args) -> None:
        await self._stack.aclose()

    def request_stop(self) -> None:
        self._stop.set()
        self._router_wake.set()
        for consumer in self.consumers:
            consumer.request_stop()

    def add_readiness_check(
        self,
        name: str,
        check: Callable[[], Awaitable[bool]],
    ) -> None:
        if not name.strip() or not callable(check):
            raise ValueError("Readiness check must have a name and callable")
        if self._running:
            raise RuntimeError("Readiness checks must be added before run")
        if name in self._readiness_checks:
            raise ValueError(f"Duplicate readiness check: {name}")
        self._readiness_checks[name] = check

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        if self._running:
            raise RuntimeError("Worker already running")
        if stop_event is not None and stop_event.is_set():
            self.request_stop()
        if self._stop.is_set():
            return
        self._running = True
        server = None
        tasks: list[asyncio.Task] = []
        stopper = asyncio.create_task(self._stop.wait())
        async def relay_stop():
            await stop_event.wait()
            self.request_stop()
        external_stop = asyncio.create_task(relay_stop()) if stop_event is not None else None
        try:
            if self.settings.health_port:
                server = await asyncio.start_server(
                    self._health,
                    "0.0.0.0",
                    self.settings.health_port,
                    limit=8192,
                )
            tasks = [asyncio.create_task(c.run()) for c in self.consumers]
            tasks += [
                asyncio.create_task(self._loop(self.router.once, wake=self._router_wake)),
                asyncio.create_task(self._loop(self._metrics, interval=10)),
            ]
            done, _ = await asyncio.wait(
                [*tasks, stopper],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if task is not stopper:
                    await task
                    if not self._stop.is_set():
                        raise RuntimeError("Worker loop stopped unexpectedly")
            # Embedded mode shares the service's single drain deadline. Standalone
            # mode retains its existing EW_SHUTDOWN_SECONDS grace period.
            await asyncio.gather(*(c.shutdown(
                None if stop_event is not None else self.settings.shutdown_seconds
            ) for c in self.consumers))
        finally:
            async def cleanup():
                self.request_stop()
                owned = [*tasks, stopper]
                if external_stop is not None:
                    owned.append(external_stop)
                for task in owned:
                    if not task.done() and not task.cancelling():
                        task.cancel()
                await asyncio.gather(*owned, return_exceptions=True)
                if server is not None:
                    server.close()
                    await server.wait_closed()
                self._running = False
            await protected_cleanup(cleanup())

    async def _loop(
        self,
        operation: Callable[[], Awaitable[int]],
        *,
        interval: float | None = None,
        wake: asyncio.Event | None = None,
        after_work: Callable[[], None] | None = None,
    ) -> None:
        delay = interval or self.settings.poll_seconds
        while not self._stop.is_set():
            # Clear BEFORE the scan. A commit during its awaits stays signalled,
            # so the following sleep cannot miss newly available work.
            if wake is not None:
                wake.clear()
            failed = False
            try:
                count = await operation()
                if count and after_work is not None:
                    after_work()
                delay = interval or (
                    self.settings.poll_seconds
                    if count
                    else min(delay * 2, self.settings.idle_poll_seconds)
                )
            except Exception:
                failed = True
                logger.exception("Worker maintenance iteration failed")
                delay = min(max(delay * 2, 0.5), 30)
            with suppress(TimeoutError):
                # Errors retain their bounded retry backoff even under traffic.
                # On idle, Redis ingestion or a settled command wakes this Pod;
                # timeout still covers work committed by another Pod/restarts.
                signal = self._stop if wake is None or failed else wake
                await asyncio.wait_for(signal.wait(), delay)
                if signal is wake and not self._stop.is_set():
                    # A short bounded window coalesces commit bursts. Waking for
                    # every ignored Step event would create unnecessary DB scans.
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), min(.02, self.settings.poll_seconds))

    async def _metrics(self) -> int:
        counts = await self.store.counts()
        self.telemetry.backlog.clear()
        for state, count in counts.items():
            self.telemetry.backlog.labels(state).set(count)
        for kind, stream, group in (
            (
                "ingress",
                self.settings.executor_event_stream,
                self.settings.event_group,
            ),
        ):
            progress = await group_progress(self.redis, stream, group)
            for metric, value in {
                "pending": progress.pending,
                "lag": progress.lag if progress.lag is not None else -1,
                "lag_known": int(progress.lag is not None),
                "has_unread": int(progress.has_unread),
            }.items():
                self.telemetry.stream.labels(kind, metric).set(value)
        return 0

    async def ready(self) -> bool:
        if not self._running or self._stop.is_set() or not all(
            c.is_healthy for c in self.consumers
        ):
            return False
        try:
            async with asyncio.timeout(2):
                await self.redis.ping()
                async with self.pool.connection() as conn:
                    await conn.execute("SELECT 1 FROM ew_bindings LIMIT 0")
                    cur = await conn.execute("""SELECT NOT EXISTS (
                        SELECT 1 FROM ew_commands e WHERE e.namespace=%s AND e.state='READY'
                        AND NOT EXISTS (SELECT 1 FROM agent_commands c
                            WHERE c.namespace=e.namespace AND c.command_id=e.command_id))""",
                        (self.store.namespace,))
                    if not (await cur.fetchone())[0]:
                        return False
                if self._readiness_checks:
                    results = await asyncio.gather(
                        *(
                            check()
                            for check in self._readiness_checks.values()
                        ),
                        return_exceptions=True,
                    )
                    if not all(result is True for result in results):
                        return False
            return True
        except Exception:
            return False

    async def _health(self, reader, writer) -> None:
        try:
            async with asyncio.timeout(3):
                request = await reader.readuntil(b"\r\n\r\n")
                path = request.split(b" ", 2)[1]
                status, body = "200 OK", b"ok"
                if path == b"/health/ready":
                    if not await self.ready():
                        status, body = "503 Unavailable", b"not ready"
                elif path == b"/metrics":
                    body = generate_latest(self.telemetry.registry)
                elif path != b"/health/live":
                    status, body = "404 Not Found", b"not found"
                writer.write(
                    f"HTTP/1.1 {status}\r\nConnection: close\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        except (
            TimeoutError,
            ValueError,
            IndexError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

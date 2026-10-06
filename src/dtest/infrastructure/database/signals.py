"""One process/loop-owned LISTEN connection for durable DB invalidation hints.

Subscriptions own its lifespan. Worker and SSE may share the connection while
retaining independent subscriptions. No business payload or correctness depends
on delivery; reconnect emits an invalidation and callers reconcile their tables.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
from weakref import WeakKeyDictionary, WeakValueDictionary

import asyncpg
from sqlalchemy.engine import make_url

from dtest.lifecycle import protected_cleanup

log = logging.getLogger(__name__)
RUN_CHANNEL = "dtest_run_changed"
COMMAND_CHANNEL = "dtest_agent_command_changed"
CHANNELS = (RUN_CHANNEL, COMMAND_CHANNEL)
Callback = Callable[[str | None], None]


class PostgresSignals:
    def __init__(self, database_url: str, *, connect=None):
        self.dsn = (
            make_url(database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        self.connect = connect or asyncpg.connect
        self.ready = asyncio.Event()
        self.connection = None
        self.task = None
        self.lock = asyncio.Lock()
        self.subscriptions: dict[object, tuple[str, Callback]] = {}

    @staticmethod
    def _deliver(callback: Callback, payload: str | None):
        try:
            callback(payload)
        except Exception as exc:
            log.warning(
                "postgres_signal_callback_failed error_type=%s",
                type(exc).__name__,
            )

    def _emit(self, channel: str | None, payload: str | None):
        for selected, callback in tuple(self.subscriptions.values()):
            if channel is None or selected == channel:
                self._deliver(callback, payload)

    async def _listen(self):
        retry = 0.5
        while True:
            conn = None
            try:
                conn = await self.connect(self.dsn, timeout=5, ssl=False)
                self.connection = conn
                lost = asyncio.Event()
                conn.add_termination_listener(lambda _: lost.set())
                for channel in CHANNELS:
                    await conn.add_listener(
                        channel,
                        lambda _c, _pid, ch, payload: self._emit(ch, payload),
                    )
                self.ready.set()
                # Covers initial scan/LISTEN and disconnected commit windows.
                self._emit(None, None)
                retry = 0.5
                await lost.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "postgres_signal_listener_unavailable error_type=%s",
                    type(exc).__name__,
                )
            finally:
                was_ready = self.ready.is_set()
                self.ready.clear()
                self.connection = None
                if was_ready:
                    self._emit(None, None)
                if conn is not None:

                    async def close():
                        try:
                            await conn.close(timeout=2)
                        except Exception as exc:
                            conn.terminate()
                            log.warning(
                                "postgres_signal_close_failed error_type=%s",
                                type(exc).__name__,
                            )

                    await protected_cleanup(close())
            await asyncio.sleep(retry)
            retry = min(5, retry * 2)

    @asynccontextmanager
    async def subscribe(self, channel: str, callback: Callback):
        if channel not in CHANNELS:
            raise ValueError("Unsupported PostgreSQL signal channel")
        token = object()
        async with self.lock:
            self.subscriptions[token] = (channel, callback)
            if self.task is None or self.task.done():
                self.task = asyncio.create_task(
                    self._listen(), name="postgres-signal-listener"
                )
            if self.ready.is_set():
                self._deliver(callback, None)
        try:
            yield self
        finally:

            async def release():
                async with self.lock:
                    self.subscriptions.pop(token, None)
                    if not self.subscriptions and self.task is not None:
                        task, self.task = self.task, None
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

            await protected_cleanup(release())


# Weak values avoid retaining old event loops/settings in tests or app reloads.
_runtimes: WeakKeyDictionary = WeakKeyDictionary()


def process_signals(database_url: str) -> PostgresSignals:
    loop = asyncio.get_running_loop()
    dsn = (
        make_url(database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    runtimes = _runtimes.setdefault(loop, WeakValueDictionary())
    signals = runtimes.get(dsn)
    if signals is None:
        signals = PostgresSignals(database_url)
        runtimes[dsn] = signals
    return signals

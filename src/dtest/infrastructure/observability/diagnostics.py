"""Opt-in local Run timings and stalled-coroutine snapshots; never record payloads.

RUN_DIAGNOSTICS_DIR unset: no callbacks, files, watchdogs or SQL listeners.
Durations overlap (nodes/checkpoint/SQL are children of graph.invoke).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import os
import time
from dtest.settings.loader import get_settings

log = logging.getLogger(__name__)
_current = ContextVar("run_diagnostic_trace", default=None)


def enabled():
    return get_settings().diagnostics_dir is not None


def utc():
    return datetime.now(timezone.utc).isoformat()


class Trace:
    def __init__(self, run_id, session_id):
        self.run_id, self.session_id = str(run_id), str(session_id)
        self.started_at, self.start = utc(), time.perf_counter()
        self.active, self.totals, self.pools = {}, {}, {}
        self.sequence = 0
        self.loop_lag_max_ms = 0.0
        self.closed = False

    def begin(self, name):
        self.sequence += 1
        self.active[self.sequence] = (name, time.perf_counter())
        return self.sequence

    def end(self, key, outcome="ok"):
        item = self.active.pop(key, None)
        if item:
            self.record(
                item[0], (time.perf_counter() - item[1]) * 1000, outcome
            )

    def record(self, name, ms, outcome="ok"):
        total = self.totals.setdefault(
            name, {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "outcomes": {}}
        )
        total["count"] += 1
        total["total_ms"] += ms
        total["max_ms"] = max(total["max_ms"], ms)
        total["outcomes"][outcome] = total["outcomes"].get(outcome, 0) + 1

    def emit(self, event, **fields):
        try:
            folder = get_settings().diagnostics_dir
            if folder is None:
                return
            folder.mkdir(parents=True, exist_ok=True)
            row = {
                "at": utc(),
                "event": event,
                "pid": os.getpid(),
                "run_id": self.run_id,
                "session_id": self.session_id,
                **fields,
            }
            # One file per process, one write per record. No SQL, locals or input/output.
            with (folder / f"runs-{os.getpid()}.jsonl").open("a") as stream:
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        except Exception:
            log.exception("Run diagnostics write failed")


@contextmanager
def span(name):
    trace = _current.get()
    if trace is None or trace.closed:
        yield
        return
    key = trace.begin(name)
    outcome = "ok"
    try:
        yield
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        trace.end(key, outcome)


def timed(name):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            with span(name):
                return await fn(*args, **kwargs)

        return wrapped

    return decorate


def instrument_async_methods(instance, prefix, names):
    if not enabled():
        return instance
    for name in names:
        setattr(
            instance, name, timed(prefix + "." + name)(getattr(instance, name))
        )
    return instance


def observe_pool(pool, name):
    instrument_async_methods(
        pool, name, ("open", "close", "getconn", "putconn")
    )
    return register_pool_trace(pool, name)


def register_pool_trace(pool, name):
    """Associate an existing shared pool with this Run without re-wrapping I/O."""
    trace = _current.get()
    if trace is not None:
        trace.pools[name] = pool.get_stats
    return pool


def coroutine_stack(coro):
    result, seen = [], set()
    while coro is not None and id(coro) not in seen and len(result) < 40:
        seen.add(id(coro))
        frame = (
            getattr(coro, "cr_frame", None)
            or getattr(coro, "gi_frame", None)
            or getattr(coro, "ag_frame", None)
        )
        if frame:
            result.append(
                {
                    "file": frame.f_code.co_filename,
                    "line": frame.f_lineno,
                    "function": frame.f_code.co_name,
                }
            )
        coro = (
            getattr(coro, "cr_await", None)
            or getattr(coro, "gi_yieldfrom", None)
            or getattr(coro, "ag_await", None)
        )
    return result


async def watchdog(trace, owner):
    threshold = get_settings().diagnostics_stall_seconds
    previous = time.perf_counter()
    last_dump = trace.start
    while True:
        await asyncio.sleep(0.1)
        now = time.perf_counter()
        trace.loop_lag_max_ms = max(
            trace.loop_lag_max_ms, (now - previous - 0.1) * 1000
        )
        previous = now
        # Repeated bounded snapshots also catch hangs before any inner span begins.
        if now - trace.start < threshold or now - last_dump < threshold:
            continue
        last_dump = now
        tasks = []
        for task in sorted(asyncio.all_tasks(), key=lambda t: t is not owner):
            chain = coroutine_stack(task.get_coro())
            # Include graph/checkpointer tasks, but omit HTTP request tasks and data.
            if task is owner or any(
                any(
                    s in f["file"]
                    for s in (
                        "langgraph",
                        "psycopg",
                        "run_service.py",
                        "agent_graph_service.py",
                    )
                )
                for f in chain
            ):
                tasks.append(
                    {
                        "owner": task is owner,
                        "done": task.done(),
                        "stack": chain,
                    }
                )
            if len(tasks) >= 80:
                break
        pools = {}
        for name, getter in trace.pools.items():
            with suppress(Exception):
                pools[name] = getter()
        trace.emit(
            "stall_snapshot",
            elapsed_ms=(now - trace.start) * 1000,
            active=[
                {"name": name, "elapsed_ms": (now - start) * 1000}
                for name, start in trace.active.values()
            ],
            loop_lag_max_ms=trace.loop_lag_max_ms,
            pools=pools,
            tasks=tasks,
        )


@asynccontextmanager
async def run_trace(run_id, session_id):
    if not enabled():
        yield None
        return
    trace = Trace(run_id, session_id)
    token = _current.set(trace)
    monitor = asyncio.create_task(watchdog(trace, asyncio.current_task()))
    trace.emit("run_start")
    outcome = "returned"
    try:
        yield trace
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await monitor
        trace.closed = True
        trace.emit(
            "run_end",
            started_at=trace.started_at,
            elapsed_ms=(time.perf_counter() - trace.start) * 1000,
            outcome=outcome,
            timings=trace.totals,
            loop_lag_max_ms=trace.loop_lag_max_ms,
            unfinished_spans=[name for name, _ in trace.active.values()],
        )
        _current.reset(token)


def graph_callbacks(callbacks):
    trace = _current.get()
    if trace is None:
        return callbacks
    from langchain_core.callbacks import AsyncCallbackHandler

    class NodeTiming(AsyncCallbackHandler):
        run_inline = True

        def __init__(self):
            self.keys = {}

        async def on_chain_start(
            self, serialized, inputs, *, run_id, **kwargs
        ):
            name = (
                kwargs.get("name") or (serialized or {}).get("name") or "chain"
            )
            self.keys[run_id] = trace.begin("chain." + str(name))

        async def on_chain_end(self, outputs, *, run_id, **kwargs):
            key = self.keys.pop(run_id, None)
            if key is not None:
                trace.end(key)

        async def on_chain_error(self, error, *, run_id, **kwargs):
            key = self.keys.pop(run_id, None)
            if key is not None:
                trace.end(key, type(error).__name__)

    return [*(callbacks or []), NodeTiming()]


def install_sql_timings(engine):
    if not enabled():
        return
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):
        trace = _current.get()
        if trace is not None and not trace.closed:
            # Retain only the SQL verb. Never log parameters or statement contents.
            context._run_diag = (
                trace,
                trace.begin(
                    "sql." + statement.lstrip().split(None, 1)[0].upper()
                ),
            )

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):
        entry = getattr(context, "_run_diag", None)
        if entry:
            entry[0].end(entry[1])
            context._run_diag = None

    @event.listens_for(engine.sync_engine, "handle_error")
    def error(context):
        entry = getattr(context.execution_context, "_run_diag", None)
        if entry:
            entry[0].end(entry[1], type(context.original_exception).__name__)

"""Opt-in benchmark tracing: identities/status/exception frames, never code or bodies.

The transport result alone is not proof that the graph committed its checkpoint.
Correlate accepted POSTs with the enclosing command error before blaming delivery.
"""
from contextvars import ContextVar
import hashlib
from pathlib import Path
import time
from urllib.parse import urlsplit

command = ContextVar("diagnostic_executor_command", default={})
request = ContextVar("diagnostic_executor_request", default=None)


def exception_chain(exc):
    rows, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        frames, tb = [], exc.__traceback__
        while tb is not None:
            frames.append({"file": Path(tb.tb_frame.f_code.co_filename).name,
                           "function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
            tb = tb.tb_next
        rows.append({"type": type(exc).__name__, "frames": frames})
        exc = exc.__cause__ if exc.__cause__ is not None else (None if exc.__suppress_context__ else exc.__context__)
    return rows


def begin(item):
    context = getattr(item, "context", None)
    return command.set({"command_id": str(item.command_id), "session_id": str(item.session_id),
                        "execution_id": str(context.execution_id) if context else None,
                        "event_sequence": context.event.event_sequence if context else None})


def failed(metrics, exc):
    metrics.setdefault("execution_errors", []).append({**command.get(), "at": time.perf_counter(),
                                                      "exception_chain": exception_chain(exc)})


def install(metrics, enabled, *, patch=None):
    from dtest.infrastructure.executor.client import ExecutorClient
    assign = patch if patch is not None else setattr
    enter, original = ExecutorClient.__aenter__, ExecutorClient.request
    async def entered(self):
        result = await enter(self)
        async def headers(response):
            row = request.get()
            if row is not None:
                row.update(status_code=response.status_code, headers_at=time.perf_counter())
        self.http.event_hooks["response"].append(headers)
        return result
    async def traced(self, method, url, payload=None):
        if not enabled():
            return await original(self, method, url, payload)
        row = {**command.get(), "method": method, "path": urlsplit(url).path,
               "start": time.perf_counter(), "status_code": None, "error": None}
        if isinstance(payload, dict):
            key = payload.get("idempotency_key")
            row["idempotency_key_sha256"] = hashlib.sha256(str(key).encode()).hexdigest() if key else None
            row["expected_version"] = payload.get("expected_version")
        token = request.set(row)
        try:
            response = await original(self, method, url, payload)
            row["status_code"] = response["status_code"]
            body = response.get("body")
            if isinstance(body, dict):
                row["receipt"] = {"execution_id": body.get("execution_id"),
                                  "state": body.get("state"), "operation_present": bool(body.get("operation"))}
            return response
        except BaseException as exc:
            row.update(error=type(exc).__name__, exception_chain=exception_chain(exc))
            raise
        finally:
            row["end"] = time.perf_counter()
            metrics.setdefault("executor_requests", []).append(row)
            request.reset(token)
    assign(ExecutorClient, "__aenter__", entered)
    assign(ExecutorClient, "request", traced)

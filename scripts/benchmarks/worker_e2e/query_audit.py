"""Opt-in SQL origin diagnostic, never a production setting or speed result.

Record statement structure, parameter hashes and transaction IDs, not values.
Nested scopes and awaited SQL time overlap; neither is exclusive CPU time.
Wrappers only observe calls, never change locking/commit/refresh behavior.
"""
from contextvars import ContextVar
from functools import wraps
import hashlib
import itertools
import time

from sqlalchemy import event

_scope = ContextVar("query_audit_scope", default=())
_ids = itertools.count(1)


def install(engine, metrics, enabled):
    from api_service.runs import execution, projection
    from api_service.services import resource_lifecycle, plan_event_persistence
    from api_service.services.message_service import MessageService

    def wrap(original, name):
        @wraps(original)
        async def observed(*args, **kwargs):
            if not enabled():
                return await original(*args, **kwargs)
            call_id = next(_ids)
            parents = _scope.get()
            token = _scope.set((*parents, (name, call_id)))
            started = time.perf_counter()
            error = None
            try:
                return await original(*args, **kwargs)
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                metrics.setdefault("query_audit_calls", []).append({
                    "name": name, "call_id": call_id,
                    "parents": [v[1] for v in parents],
                    "wall_ms": (time.perf_counter() - started) * 1000,
                    "error": error,
                })
                _scope.reset(token)
        return observed

    execution.prepare = wrap(execution.prepare, "prepare")
    projection.finalize_state = wrap(projection.finalize_state, "finalize_state")
    execution.finalize_state = projection.finalize_state
    projection.synchronize_agentic_execution = wrap(
        projection.synchronize_agentic_execution, "executor_projection")
    resource_lifecycle.lock_session = wrap(resource_lifecycle.lock_session, "lock_session")
    plan_event_persistence.persist_plan_events = wrap(plan_event_persistence.persist_plan_events, "plan_events")
    MessageService._create_locked = staticmethod(wrap(MessageService._create_locked, "message_insert"))

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, many):
        scope = _scope.get()
        if not enabled() or not scope:
            context._query_audit = None
            return
        transaction = conn.get_transaction()
        if conn.info.get("query_audit_transaction") is not transaction:
            conn.info["query_audit_transaction"] = transaction
            conn.info["query_audit_transaction_id"] = next(_ids)
        context._query_audit = {
            "scope": [{"name": name, "call_id": key} for name, key in scope],
            "transaction_id": conn.info["query_audit_transaction_id"],
            "fingerprint": " ".join(statement.split()),
            "parameter_sha256": hashlib.sha256(repr(parameters).encode()).hexdigest(),
            "executemany": many,
            "started": time.perf_counter(),
        }

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, many):
        row = getattr(context, "_query_audit", None)
        if row is not None:
            row["sql_wall_ms"] = (time.perf_counter() - row.pop("started")) * 1000
            metrics.setdefault("query_audit_sql", []).append(row)

"""Record a consumed user answer in the same node update as its business result.

Use user_interrupt inside a node decorated with record_user_resume. Direct
LangGraph callers may still pass raw values; service calls use a private envelope.
A node that raises/interrupts again never emits a completion receipt.
"""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import inspect
from typing import Any

from langgraph.types import interrupt
from dtest.contracts.user_resume import UserResumeNeedsRecovery, resume_identity

_receipts: ContextVar[list | None] = ContextVar("user_resume_receipts", default=None)


def user_interrupt(value: Any) -> Any:
    answer = interrupt(value)
    if not isinstance(answer, dict) or "__user_resume_v1__" not in answer:
        return answer
    identity = answer["__user_resume_v1__"]
    if not isinstance(identity, dict) or not all(
        isinstance(identity.get(key), str) and identity[key]
        for key in ("command_id", "interrupt_id", "digest")
    ):
        raise UserResumeNeedsRecovery("Invalid user resume identity")
    expected = resume_identity(identity["command_id"], identity["interrupt_id"], answer.get("value"))
    receipts = _receipts.get()
    if identity != expected or receipts is None:
        raise UserResumeNeedsRecovery("User resume receipt scope or payload mismatch")
    receipts.append(identity)
    return answer["value"]


def _result(result, receipts):
    if not receipts:
        return result
    if len(receipts) != 1 or not isinstance(result, dict):
        raise UserResumeNeedsRecovery("User resume nodes must consume one answer and return a state update")
    return {**result, "user_resume_receipt": receipts[0]}


def record_user_resume(node):
    """Add receipt to a successful node result, before LangGraph checkpoints it."""
    if inspect.iscoroutinefunction(node):
        @wraps(node)
        async def asynchronous(*args, **kwargs):
            receipts = []
            token = _receipts.set(receipts)
            try:
                return _result(await node(*args, **kwargs), receipts)
            finally:
                _receipts.reset(token)
        return asynchronous

    @wraps(node)
    def synchronous(*args, **kwargs):
        receipts = []
        token = _receipts.set(receipts)
        try:
            return _result(node(*args, **kwargs), receipts)
        finally:
            _receipts.reset(token)
    return synchronous

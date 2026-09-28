"""Immutable execution identity captured inside the queue claim transaction.

This guards CRUD ownership boundaries. It is not checkpoint write fencing and
must never be used to justify replaying a still-running graph.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ExecutionClaim:
    run_id: UUID
    task_id: UUID
    lock_token: UUID
    attempt: int


current_execution_claim: ContextVar[ExecutionClaim | None] = ContextVar("execution_claim", default=None)


@contextmanager
def bind_execution_claim(claim: ExecutionClaim):
    token = current_execution_claim.set(claim)
    try:
        yield
    finally:
        current_execution_claim.reset(token)

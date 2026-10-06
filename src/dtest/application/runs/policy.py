"""Shared retry eligibility and backoff policy for Run execution."""

from dtest.application.runs.errors import RunError
from dtest.application.runs.tasks import TaskService


def retry_delay_seconds(attempt_count: int) -> float:
    """Worker와 Reconciler가 공유하는 재시도 간격입니다."""
    return TaskService.retry_delay_seconds(attempt_count)


def is_retryable(exc: Exception) -> bool:
    """사용자 입력/상태 충돌인 4xx는 반복해도 같으므로 자동 재시도하지 않습니다."""
    from dtest.contracts.model_selection import ModelSelectionError
    from dtest.infrastructure.executor.client import (
        ExecutorSubmitError,
        ExecutorOutcomeUnknown,
    )

    if isinstance(exc, ExecutorOutcomeUnknown):
        return False
    if isinstance(exc, ExecutorSubmitError):
        return exc.retryable
    if isinstance(exc, ModelSelectionError):
        return False
    if isinstance(exc, RunError):
        return exc.retryable
    return getattr(exc, "status_code", 500) >= 500

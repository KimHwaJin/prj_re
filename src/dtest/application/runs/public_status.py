"""Public Run status shared by detailed Run and lightweight session views."""

from dtest.contracts.enums import AgentRunStatus, TaskStatus

TERMINAL_TASKS = (
    TaskStatus.SUCCESS,
    TaskStatus.ERROR,
    TaskStatus.TIMEOUT,
    TaskStatus.CANCELED,
)


def public_status(
    invocation_status,
    *,
    task_status=None,
    recovery_required=False,
    executor_wait=False,
):
    if recovery_required:
        return "recovery_required"
    if task_status in TERMINAL_TASKS:
        return task_status.value
    if invocation_status == AgentRunStatus.INTERRUPTED:
        return "waiting_executor" if executor_wait else "waiting_input"
    return invocation_status.value

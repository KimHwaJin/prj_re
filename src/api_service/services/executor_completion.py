"""Project completed Executor graph state to the API's durable session lifecycle."""
from uuid import UUID

from sqlalchemy import select

from api_service.core.database import get_session_factory
from api_service.core.enums import AgentRunStatus, TaskStatus
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.task_model import TaskModel
from api_service.services.run_service import RunService
from api_service.services.task_event_service import TaskEventService
from api_service.services.task_service import TaskService
from api_service.worker import DeferEvent, EventContext


async def synchronize_executor_completion(context: EventContext, graph) -> None:
    snapshot = await graph.aget_state(context.graph_config)
    values = snapshot.values
    # A step event may only advance to another wait; do not release that session.
    if snapshot.next or not values:
        return
    if values.get("task_id") != context.task_id or values.get("execution_id") != str(context.execution_id):
        raise DeferEvent("Executor completion does not match the current graph")
    if values.get("ew_receipts", {}).get(str(context.command_id)) != str(context.event.event_id):
        raise DeferEvent("Executor completion has no durable graph receipt")
    async with get_session_factory()() as db:
        task_id = await db.scalar(select(TaskModel.task_id).where(
            TaskModel.session_id == UUID(context.session_id),
            TaskModel.graph_task_id == UUID(context.task_id),
        ))
        if task_id is None:
            pending_link = await db.scalar(select(TaskModel.task_id).where(
                TaskModel.session_id == UUID(context.session_id),
                TaskModel.graph_task_id.is_(None),
                TaskModel.status.not_in(TaskService.TERMINAL_STATUSES),
            ).limit(1))
            if pending_link is not None:
                raise DeferEvent("API task has not recorded its graph identity")
            # Standalone Agent graphs need not have API-owned Task rows.
            return
        run_id = await db.scalar(select(AgentRunModel.run_id).where(
            AgentRunModel.task_id == task_id,
        ).order_by(AgentRunModel.created_at.desc(), AgentRunModel.run_id.desc()).limit(1))
        if run_id is None:
            raise DeferEvent("Executor task has no API Run")
        run, task = await RunService._lock_run_and_task(db, run_id)
        if task is None or task.recovery_required:
            raise DeferEvent("Executor task requires recovery")
        if task.status in TaskService.TERMINAL_STATUSES:
            return
        # Event delivery can beat the initial Run's post-interrupt DB commit.
        # Retry this projection from the graph receipt after that commit.
        if task.status != TaskStatus.WAITING_INPUT or run.status != AgentRunStatus.INTERRUPTED:
            raise DeferEvent("Initial Run has not recorded its Executor wait")
        status = str(values.get("execution_status") or "")
        if status == "SUCCEEDED":
            target = AgentRunStatus.SUCCESS
        elif status in {"CANCELED", "CANCELLED"}:
            target = AgentRunStatus.CANCELED
        elif status in {"FAILED", "ERROR", "TIMED_OUT", "TIMEOUT"}:
            target = AgentRunStatus.ERROR
        else:
            raise DeferEvent("Executor graph has no terminal execution status")
        RunService._finish_run(run, target)
        run.agent_response = {**(run.agent_response or {}),
                              "final_response": values.get("final_response"),
                              "report_status": values.get("report_status")}
        TaskService.transition(task, TaskStatus(target.value))
        await TaskEventService.append_for_run(
            db, run_id=run_id, event_type=f"task.{target.value}",
            payload={"status": target.value, "execution_id": str(context.execution_id)}, commit=False,
        )
        await db.commit()

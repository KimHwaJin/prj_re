"""Project checkpoint outcomes into the public Run and Task lifecycle."""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.core.database import get_session_factory
from api_service.core.enums import AgentRunStatus, DeleteYN, MessageStatus, MessageType, TaskStatus
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.message_model import MessageModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.task_model import TaskModel
from api_service.runs.repository import lock_run_and_task
from api_service.services.agent_graph_service import interrupt_payload, run_status_from_state
from api_service.services.helpers import utc_now
from api_service.services.task_event_service import TaskEventService
from api_service.services.task_service import TaskService
from api_service.services.workflow_service import WorkflowService
from api_service.worker import DeferEvent, EventContext
from service_contracts.execution import ExecutionNeedsRecovery
from service_runtime.diagnostics import span


TERMINAL_STATUSES = {AgentRunStatus.SUCCESS, AgentRunStatus.ERROR, AgentRunStatus.TIMEOUT, AgentRunStatus.CANCELED}

def finalize_canceled(
    run: AgentRunModel,
    task: TaskModel | None,
) -> None:
    """실제 실행 중단을 확인한 뒤 Run/Task를 canceled로 확정하고 잠금을 풉니다."""
    finish_run(run, AgentRunStatus.CANCELED)
    if task is not None:
        TaskService.transition(task, TaskStatus.CANCELED)


def finish_run(run: AgentRunModel, status: AgentRunStatus, *, failure: dict | None = None, interrupt=None) -> None:
    """AgentRun 상태는 실행 기록만 변경하며 Task 잠금은 TaskService가 처리합니다."""
    run.status = status
    run.failure = failure
    run.interrupt = interrupt
    if status in TERMINAL_STATUSES:
        run.completed_at = utc_now()


async def append_error_system_message(
    db: AsyncSession,
    *,
    run: AgentRunModel,
    task: TaskModel | None,
    error: Exception,
) -> None:
    """최종 실행 오류를 채팅 화면에 표시할 system 메시지로 한 번만 저장합니다."""
    client_request_id = uuid5(NAMESPACE_URL, f"dtest-agent:task-error:{run.run_id}")
    existing = await db.scalar(
        select(MessageModel).where(
            MessageModel.session_id == run.session_id,
            MessageModel.client_request_id == client_request_id,
        )
    )
    if existing is not None:
        return

    error_text = str(error)
    message = MessageModel(
        session_id=run.session_id,
        message_type=MessageType.SYSTEM,
        content=[{"type": "text", "text": error_text}],
        content_text=error_text,
        message_status=MessageStatus.FAILED,
        client_request_id=client_request_id,
        error_code="TASK_EXECUTION_ERROR",
        error_message=error_text,
        metadata_json={
            "event": "task.error",
            "run_id": str(run.run_id),
            "task_id": str(task.task_id) if task is not None else None,
        },
        delete_yn=DeleteYN.N,
    )
    db.add(message)
    await db.flush()

    # 새로고침해도 오류가 해당 Session의 마지막 채팅 항목으로 보이게 합니다.
    session = await db.get(SessionModel, run.session_id)
    if session is not None:
        session.current_leaf_message_id = message.message_id


async def require_invocation_recovery(db, run_id, reason):
    try:
        await db.rollback()
        if not await TaskService.require_recovery(db, run_id=run_id, reason=reason):
            raise ExecutionNeedsRecovery("Could not quarantine invocation")
        run = await db.get(AgentRunModel, run_id, populate_existing=True)
        if run is None:
            raise ExecutionNeedsRecovery("Quarantined invocation disappeared")
        return run
    except ExecutionNeedsRecovery:
        raise
    except Exception as exc:
        # Never release the owner if the durable Task guard was not verified.
        raise ExecutionNeedsRecovery("Could not verify invocation quarantine") from exc


async def finalize_state(db, execution_run_id, user_id, state):
    status = run_status_from_state(state)
    route = (state.get("routing_result") or {}).get("route")
    # 완료 직전 row lock을 획득해 동시에 들어온 cancel 요청과 최종 상태를 직렬화합니다.
    run, task = await lock_run_and_task(db, execution_run_id)
    boundaries = [getattr(item, "id", None) for item in state.get("__interrupt__", ())]
    run.metadata_json = {**(run.metadata_json or {}),
                         "_checkpoint_interrupt_id": boundaries[0] if len(boundaries) == 1 else None}
    if run.cancel_requested_at is not None:
        finalize_canceled(run, task)
        await TaskEventService.append_for_run(
            db, run_id=execution_run_id, event_type="task.canceled",
            payload={"status": "canceled"}, commit=False,
        )
        await db.commit()
        await db.refresh(run)
        return run
    finish_run(run, status, interrupt=interrupt_payload(state))
    if state.get('agent_runtime') == 'agentic-planning-v1':
        run.metadata_json = {**(run.metadata_json or {}), '_agent_runtime': 'agentic-planning-v1',
                             '_plan_reviews': state.get('reviews', []),
                             '_planning_interaction':state.get('interaction_data'),
                             '_planning_revision_count':state.get('planning_revision_count',0),
                             '_decision_review':state.get('decision_review'),
                             '_repair_review':state.get('repair_review'),
                             '_approved_plan': state.get('approved_snapshot')}
    run.agent_response = {
        "route": route,
        "final_response": state.get("final_response"),
        "service_response": state.get("service_response"),
        # E13: 기존 추천 자산 재사용과 LLM 신규 생성을 영속적으로 구분합니다.
        "workflow_origin": state.get("workflow_origin"),
        # recommended 실행은 어떤 전역 template에서 시작했는지도 역추적합니다.
        "selected_workflow_id": (
            (state.get("recommendation") or {}).get("recommendation") or {}
        ).get("workflow_id"),
    }
    if task is not None:
        task.trigger_type = route or task.trigger_type
        task_status = TaskStatus.WAITING_INPUT if status == AgentRunStatus.INTERRUPTED else TaskStatus(status.value)
        TaskService.transition(task, task_status)
    if task is not None:
        task_event_status = (
            TaskStatus.WAITING_INPUT.value
            if run.status == AgentRunStatus.INTERRUPTED
            else run.status.value
        )
        await TaskEventService.append_for_run(
            db,
            run_id=run.run_id,
            event_type=f"task.{task_event_status}",
            payload={
                "status": task_event_status,
                "run_status": run.status.value,
                "interrupt": run.interrupt,
                "failure": run.failure,
            },
            commit=False,
        )
    with span("run.final_commit"):
        await db.commit()
        await db.refresh(run)
    # E13: 성공한 LLM 신규 생성 Workflow만 root candidate로 자동 자산화합니다.
    # recommended는 이미 존재하는 Workflow이므로 중복 파일/DB 행을 만들지 않습니다.
    generated_workflow = state.get("workflow")
    if (
        run.status == AgentRunStatus.SUCCESS
        and route == "analysis"
        and state.get("workflow_origin") == "generated"
        and isinstance(generated_workflow, dict)
        and generated_workflow
    ):
        await WorkflowService.save_generated_after_success(
            db,
            user_id=user_id,
            source_run_id=run.run_id,
            document=generated_workflow,
        )
    return run


async def synchronize_executor_completion(context: EventContext, graph) -> None:
    snapshot = await graph.aget_state(context.graph_config)
    values = snapshot.values
    if values.get("agent_runtime") == "agentic-planning-v1":
        await synchronize_agentic_execution(context, snapshot)
        return
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
        run, task = await lock_run_and_task(db, run_id)
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
        finish_run(run, target)
        run.agent_response = {**(run.agent_response or {}),
                              "final_response": values.get("final_response"),
                              "report_status": values.get("report_status")}
        TaskService.transition(task, TaskStatus(target.value))
        await TaskEventService.append_for_run(
            db, run_id=run_id, event_type=f"task.{target.value}",
            payload={"status": target.value, "execution_id": str(context.execution_id)}, commit=False,
        )
        await db.commit()


async def synchronize_agentic_execution(context: EventContext, snapshot) -> None:
    """Project decision/Executor waits and terminal results from a durable receipt."""
    from api_service.services.graph_crud_persistence import persist_graph_state
    from api_service.services.graph_recovery import checkpoint_state, snapshot_interrupts

    values = snapshot.values
    matches_delivery = (
        values.get("task_id") == context.task_id
        and values.get("execution_id") == str(context.execution_id)
        and values.get("ew_receipts", {}).get(str(context.command_id)) == str(context.event.event_id)
    )
    if not matches_delivery:
        raise DeferEvent("Execution state has no matching durable receipt")
    if snapshot.next and not snapshot_interrupts(snapshot):
        raise DeferEvent("Executor graph progress is incomplete")

    state = checkpoint_state(snapshot)
    # Receipt replay repairs events/messages before updating the public Run state.
    await persist_graph_state(
        state, user_id=UUID(values["user_id"]), agent_run_id=values["agent_run_id"],
    )
    async with get_session_factory()() as db:
        task_id = await db.scalar(select(TaskModel.task_id).where(
            TaskModel.session_id == UUID(context.session_id),
            TaskModel.graph_task_id == UUID(context.task_id),
        ))
        if task_id is None:
            raise DeferEvent("API task has not recorded graph identity")
        run_id = await db.scalar(select(AgentRunModel.run_id).where(
            AgentRunModel.task_id == task_id,
        ).order_by(AgentRunModel.created_at.desc(), AgentRunModel.run_id.desc()).limit(1))
        run, task = await lock_run_and_task(db, run_id)
        if task.status in TaskService.TERMINAL_STATUSES:
            return
        if (task.recovery_required or task.status != TaskStatus.WAITING_INPUT
                or run.status != AgentRunStatus.INTERRUPTED):
            raise DeferEvent("API invocation has not committed its wait")
        await finalize_state(db, run_id, UUID(values["user_id"]), state)

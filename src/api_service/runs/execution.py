"""Execute an immutable claim with separate preparation and outcome units of work."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from api_service.core.database import get_session_factory, short_session
from api_service.core.enums import AgentRunStatus, TaskStatus
from api_service.core.execution_claim import ExecutionClaim, current_execution_claim
from api_service.core.execution_lifecycle import execution_health
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.runs.errors import (
    CancellationRequested,
    InvalidRunRequest,
    RunError,
    RunExecutionFailed,
    RunUnavailable,
)
from api_service.runs.monitoring import run_cancellable
from api_service.runs.policy import is_retryable, retry_delay_seconds
from api_service.runs.projection import (
    TERMINAL_STATUSES,
    append_error_system_message,
    finalize_canceled,
    finalize_state,
    finish_run,
    require_invocation_recovery,
)
from api_service.runs.repository import lock_run_and_task, require_session
from api_service.schemas.common.run_schema import RunCreate
from api_service.services.agent_graph_service import (
    ainvoke_resume,
    ainvoke_user_turn,
    user_request_from_messages,
)
from api_service.services.graph_recovery import GraphProjectionError
from api_service.services.helpers import utc_now
from api_service.services.llm_token_event_service import LLMTokenEventBuffer
from api_service.services.task_event_service import TaskEventService
from api_service.services.task_service import TaskService
from config import settings
from service_contracts.execution import ExecutionNeedsRecovery, InvocationNeedsRecovery
from service_runtime.diagnostics import span


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedInvocation:
    user_id: UUID
    session_id: UUID
    payload: RunCreate
    claim: ExecutionClaim
    model_selection: dict | None
    initial_protocol: int | None
    initial_started: bool
    resume_target: str | None
    resume_started: bool
    run_id: UUID
    task_id: UUID
    project_id: UUID
    checkpoint_id: UUID | None
    trigger_id: UUID | None

async def prepare(claim: ExecutionClaim, user_id: UUID, session_id: UUID, payload: RunCreate) -> PreparedInvocation:
    """Verify a committed claim and close its DB session before graph work."""
    if claim is None or current_execution_claim.get() != claim:
        raise ExecutionNeedsRecovery("Execution requires its immutable Worker claim.")
    async with short_session(get_session_factory()) as db:
        session = await require_session(db, user_id, session_id)
        run, task = await lock_run_and_task(db, claim.run_id)
        if run.session_id != session_id or task is None:
            raise ExecutionNeedsRecovery("Claimed Run does not match its session/task.")
        await db.refresh(run)
        await db.refresh(task)
        # rollback은 ORM 속성을 expire하므로 취소/오류 처리에 쓸 ID는 평범한 값으로 보존합니다.
        execution_model_selection = (run.metadata_json or {}).get("_model_selection")
        execution_initial_protocol = (run.metadata_json or {}).get("_initial_protocol")
        execution_initial_started = bool((run.metadata_json or {}).get("_initial_started"))
        execution_resume_target = (run.metadata_json or {}).get("_resume_target")
        execution_resume_started = bool((run.metadata_json or {}).get("_resume_started"))
        execution_run_id = run.run_id
        execution_task_id = task.task_id
        execution_project_id = session.project_id
        execution_checkpoint_id = task.checkpoint_run_id
        execution_trigger_id = run.trigger_message_id
        await TaskEventService.append(
            db,
            task_id=task.task_id,
            run_id=execution_run_id,
            event_type="task.started" if payload.command is None else "task.resumed",
            payload={"status": TaskStatus.RUNNING.value},
            commit=False,
        )
        # End preparation without refresh (which would autobegin a new read
        # transaction). Only plain values cross the graph execution boundary.
        await db.commit()

        return PreparedInvocation(user_id=user_id, session_id=session_id, payload=payload, claim=claim,
            model_selection=execution_model_selection,
            initial_protocol=execution_initial_protocol,
            initial_started=execution_initial_started,
            resume_target=execution_resume_target,
            resume_started=execution_resume_started,
            run_id=execution_run_id,
            task_id=execution_task_id,
            project_id=execution_project_id,
            checkpoint_id=execution_checkpoint_id,
            trigger_id=execution_trigger_id)

async def invoke(prepared: PreparedInvocation) -> dict:
    """Run the graph with owned observers and no caller database session."""
    user_id, session_id, payload, execution_claim = prepared.user_id, prepared.session_id, prepared.payload, prepared.claim
    execution_model_selection = prepared.model_selection
    execution_initial_protocol = prepared.initial_protocol
    execution_initial_started = prepared.initial_started
    execution_resume_target = prepared.resume_target
    execution_resume_started = prepared.resume_started
    execution_run_id = prepared.run_id
    execution_task_id = prepared.task_id
    execution_project_id = prepared.project_id
    execution_checkpoint_id = prepared.checkpoint_id
    execution_trigger_id = prepared.trigger_id
    token_events = LLMTokenEventBuffer(task_id=execution_task_id, run_id=execution_run_id, expose_tokens=False)
    token_events.start()
    try:
        async with TaskService.lease_heartbeat(execution_claim.task_id, execution_claim.lock_token, run_id=execution_run_id) as heartbeat:
            from service_runtime.model_selection import current_catalog
            current_catalog().resolve(execution_model_selection)
            if payload.command is not None:
                state = await run_cancellable(
                    execution_run_id,
                    ainvoke_resume(
                        user_id=user_id, session_id=session_id,
                        checkpoint_run_id=execution_checkpoint_id,
                        agent_run_id=execution_run_id,
                        command=payload.command,
                        resume_target=execution_resume_target,
                        resume_started=execution_resume_started,
                        callbacks=[token_events],
                        model_selection=execution_model_selection,
                    ),
                    observers=(heartbeat, token_events.consumer),
                )
            else:
                if payload.input is None:
                    raise InvalidRunRequest("Run input is required when command is omitted.")
                state = await run_cancellable(
                    execution_run_id,
                    ainvoke_user_turn(
                        user_id=user_id, project_id=execution_project_id,
                        session_id=session_id, run_id=execution_run_id,
                        user_request=user_request_from_messages(payload.input.messages),
                        initial_protocol=execution_initial_protocol,
                        initial_started=execution_initial_started,
                        trigger_message_id=execution_trigger_id,
                        callbacks=[token_events],
                        model_selection=execution_model_selection,
                    ),
                    observers=(heartbeat, token_events.consumer),
                )
    finally:
        # 마지막 짧은 token buffer까지 terminal event 전에 durable store로 flush합니다.
        with span("run.token_events_close"):
            await token_events.close()

    return state

async def record_failure(db, prepared: PreparedInvocation, exc: BaseException) -> AgentRunModel:
    """Persist cancellation/retry/quarantine only after execution has stopped."""
    payload, session_id, execution_run_id = prepared.payload, prepared.session_id, prepared.run_id
    try:
        raise exc
    except InvocationNeedsRecovery as exc:
        # The graph and observers have stopped. Quarantine this Task only;
        # existing ownership-uncertain failures still poison the process below.
        return await require_invocation_recovery(db, execution_run_id, str(exc))
    except (ExecutionNeedsRecovery, asyncio.CancelledError):
        # No retry/terminal transition without confirmed execution ownership.
        # The independent recorder remains able to mark a stuck live graph.
        execution_health.fail(execution_run_id, "execution_interrupted_or_uncertain")
        await db.rollback()
        await TaskService.require_recovery(
            db, run_id=execution_run_id, reason=execution_health.faults[str(execution_run_id)]
        )
        raise
    except CancellationRequested:
        # 취소 API가 기록한 요청을 다시 읽은 후에만 terminal 상태와 lock을 변경합니다.
        await db.rollback()
        run, task = await lock_run_and_task(db, execution_run_id)
        finalize_canceled(run, task)
        await TaskEventService.append_for_run(
            db, run_id=execution_run_id, event_type="task.canceled",
            payload={"status": "canceled"}, commit=False,
        )
        await db.commit()
        await db.refresh(run)
        return run
    except Exception as exc:
        error_id = str(uuid4())
        # Preserve the original traceback even if database recovery fails.
        logger.exception(
            "Agent run failed: error_id=%s run_id=%s session_id=%s",
            error_id,
            execution_run_id,
            session_id,
        )
        from service_runtime.model_selection import ModelSelectionError
        failure = {
            "code": (("RESUME_PROJECTION_FAILED" if payload.command is not None else "INITIAL_PROJECTION_FAILED")
                     if isinstance(exc, GraphProjectionError)
                     else "RUN_MODEL_UNAVAILABLE" if isinstance(exc, ModelSelectionError) else "AGENT_RUN_FAILED"),
            "stage": "state_projection" if isinstance(exc, GraphProjectionError) else "graph_execution",
            "exception_type": f"{type(exc).__module__}.{type(exc).__name__}",
            "message": str(exc),
            "error_id": error_id,
        }
        await db.rollback()
        # A successful final commit releases the Task lease. Read its outcome
        # before requiring that old lease again; session ownership is still held.
        committed = await db.get(AgentRunModel, execution_run_id, populate_existing=True)
        if committed is not None and (
            committed.status in TERMINAL_STATUSES
            or committed.status == AgentRunStatus.INTERRUPTED
        ):
            return committed
        run, task = await lock_run_and_task(db, execution_run_id)
        delivered = bool((run.metadata_json or {}).get(
            "_resume_started" if payload.command is not None else "_initial_started"))
        if (payload.command is not None or delivered or isinstance(exc, GraphProjectionError)) and (
            run.attempt_count > max(0, settings.agent_worker_max_retries)
            or (delivered and not is_retryable(exc))
        ):
            return await require_invocation_recovery(
                db, execution_run_id, "Invocation cannot be safely retried or retry budget exhausted")
        if run.cancel_requested_at is not None:
            # 취소와 Graph 예외가 경쟁하면 사용자가 먼저 요청한 취소를 최종 상태로 보존합니다.
            finalize_canceled(run, task)
        elif (
            task is not None
            and is_retryable(exc)
            and run.attempt_count <= max(0, settings.agent_worker_max_retries)
        ):
            # Task는 terminal로 만들지 않아 동일 Session의 원자 잠금을 계속 유지합니다.
            delay = retry_delay_seconds(run.attempt_count)
            run.status = AgentRunStatus.PENDING
            run.failure = {
                **failure,
                "retry_scheduled": True,
                "failed_attempt": run.attempt_count,
            }
            run.completed_at = None
            run.next_attempt_at = utc_now() + timedelta(seconds=delay)
            task.status = TaskStatus.PENDING
            task.failure_reason = str(exc)
            task.lock_owner = "retry:scheduled"
            task.lock_token = None
            task.heartbeat_at = None
            task.lease_expires_at = None
            await TaskEventService.append_for_run(
                db,
                run_id=execution_run_id,
                event_type="task.retry_scheduled",
                payload={
                    "status": "pending",
                    "failed_attempt": run.attempt_count,
                    "max_retries": max(0, settings.agent_worker_max_retries),
                    "delay_seconds": delay,
                    "next_attempt_at": run.next_attempt_at.isoformat(),
                    "failure": run.failure,
                },
                commit=False,
            )
            await db.commit()
            await db.refresh(run)
            return run
        else:
            finish_run(run, AgentRunStatus.ERROR, failure=failure)
            if task is not None:
                TaskService.transition(task, TaskStatus.ERROR, failure_reason=str(exc))
            # SSE task.error와 같은 오류를 messages에도 system 역할로 영속화합니다.
            await append_error_system_message(
                db,
                run=run,
                task=task,
                error=exc,
            )
        await TaskEventService.append_for_run(
            db, run_id=execution_run_id, event_type=f"task.{run.status.value}",
            payload={"status": run.status.value, "failure": run.failure}, commit=False,
        )
        await db.commit()
        if run.status == AgentRunStatus.CANCELED:
            await db.refresh(run)
            return run
        if isinstance(exc, RunError):
            raise
        raise (RunUnavailable if isinstance(exc, RuntimeError) else RunExecutionFailed)(f"Agent graph failed: {exc}") from exc


async def execute_claimed(claim: ExecutionClaim, user_id: UUID, session_id: UUID, payload: RunCreate) -> AgentRunModel:
    """Execute only a claimed invocation; admission is a separate operation."""
    prepared = await prepare(claim, user_id, session_id, payload)
    try:
        state = await invoke(prepared)
        async with short_session(get_session_factory()) as db:
            try:
                return await finalize_state(db, prepared.run_id, prepared.user_id, state)
            except ExecutionNeedsRecovery:
                raise
            except Exception as exc:
                raise GraphProjectionError("Durable invocation needs final state projection") from exc
    except BaseException as exc:
        async with short_session(get_session_factory()) as db:
            return await record_failure(db, prepared, exc)

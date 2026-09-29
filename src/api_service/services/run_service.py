import asyncio
import logging
import hashlib
import json
from datetime import timedelta
from typing import Any, Awaitable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.core.enums import (
    AgentRunStatus,
    DeleteYN,
    MessageStatus,
    MessageType,
    TaskStatus,
)
from config import settings
from api_service.core.database import get_session_factory
from api_service.core.execution_claim import current_execution_claim
from service_contracts.execution import ExecutionNeedsRecovery
from service_contracts.user_resume import UserResumeNeedsRecovery
from api_service.core.execution_lifecycle import execution_health, finish_observer, observe_termination, wait_for_stop
from service_runtime.cleanup import protected_cleanup
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.message_model import MessageModel
from api_service.models.common.task_model import TaskModel
from api_service.models.common.session_model import SessionModel
from api_service.repositories.session_repository import SessionRepository
from api_service.schemas.common.run_schema import RunCancel, RunCreate
from api_service.services.agent_graph_service import (
    ainvoke_resume,
    ainvoke_user_turn,
    interrupt_payload,
    run_status_from_state,
    user_request_from_messages,
)
from api_service.services.helpers import utc_now
from api_service.services.task_service import TaskService
from api_service.services.user_resume_service import ResumeProjectionError
from api_service.services import resource_lifecycle as lifecycle
from api_service.services.workflow_service import WorkflowService
from api_service.services.task_event_service import TaskEventService
from api_service.services.llm_token_event_service import LLMTokenEventBuffer
from service_runtime.diagnostics import span, timed

logger = logging.getLogger(__name__)


class RunService:
    """agent_runs CRUD와 Graph 실행 경계입니다. Message CRUD를 호출하지 않습니다."""

    TERMINAL_STATUSES = {AgentRunStatus.SUCCESS, AgentRunStatus.ERROR, AgentRunStatus.TIMEOUT, AgentRunStatus.CANCELED}

    class CancellationRequested(Exception):
        """내부 Graph task가 명시적인 사용자 취소 요청으로 중단됐음을 표시합니다."""

    @staticmethod
    def _retry_delay_seconds(attempt_count: int) -> float:
        """호환용 진입점이며 실제 정책은 TaskService와 Reconciler가 공유합니다."""
        return TaskService.retry_delay_seconds(attempt_count)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """사용자 입력/상태 충돌인 4xx는 반복해도 같으므로 자동 재시도하지 않습니다."""
        from service_runtime.model_selection import ModelSelectionError
        from integrations.executor.client import ExecutorSubmitError, ExecutorOutcomeUnknown
        if isinstance(exc, ExecutorOutcomeUnknown):
            return False
        if isinstance(exc, ExecutorSubmitError):
            return exc.retryable
        if isinstance(exc, ModelSelectionError):
            return False
        return not isinstance(exc, HTTPException) or exc.status_code >= 500

    @staticmethod
    async def _wait_for_cancellation(run_id: UUID, stop: asyncio.Event) -> bool:
        """별도 DB session으로 취소 요청을 감시해 다른 API worker의 요청도 감지합니다."""
        interval = max(0.05, settings.task_cancel_poll_interval_seconds)
        while not stop.is_set():
            async with asyncio.timeout(settings.run_monitor_timeout_seconds):
                async with get_session_factory()() as cancellation_db:
                    requested_at = await cancellation_db.scalar(
                        select(AgentRunModel.cancel_requested_at).where(
                            AgentRunModel.run_id == run_id
                        )
                    )
            if requested_at is not None:
                return True
            await wait_for_stop(stop, interval)
        return False

    @staticmethod
    async def _run_cancellable(
        run_id: UUID,
        graph_awaitable: Awaitable[dict[str, Any]],
        *,
        observers: tuple[asyncio.Task, ...] = (),
    ) -> dict[str, Any]:
        """Graph와 DB cancel watcher를 경쟁시켜 실제 coroutine을 cooperative cancel합니다."""
        stop = asyncio.Event()
        from integrations.executor.client import submission_scope, SubmissionEffects, ExecutorOutcomeUnknown
        effects = SubmissionEffects()
        async def invoke():
            with submission_scope(effects):
                return await graph_awaitable
        graph_task = asyncio.create_task(invoke(), name=f"graph:{run_id}")
        cancel_task = asyncio.create_task(RunService._wait_for_cancellation(run_id, stop), name=f"cancel-watch:{run_id}")

        async def cleanup():
            stop.set()
            errors = []
            try:
                await observe_termination(graph_task, run_id=run_id, stage="graph_stop", cancel=True)
            except ExecutionNeedsRecovery as exc:
                errors.append(exc)
            finally:
                # Retrieve the exception even when cancellation/another observer
                # won the race; the selected graph result is handled below.
                outcomes = await asyncio.gather(graph_task, return_exceptions=True)
                import inspect
                if inspect.iscoroutine(graph_awaitable) and inspect.getcoroutinestate(graph_awaitable) == inspect.CORO_CREATED:
                    graph_awaitable.close()
                if isinstance(outcomes[0], ExecutionNeedsRecovery):
                    errors.append(outcomes[0])
            try:
                await finish_observer(cancel_task, run_id=run_id, stage="cancel_watch_stop")
            except ExecutionNeedsRecovery as exc:
                errors.append(exc)
            if errors:
                raise errors[0]

        try:
            done, _ = await asyncio.wait(
                {graph_task, cancel_task, *observers},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for observer in observers:
                if observer in done:
                    execution_health.fail(run_id, f"observer_stopped:{observer.get_name()}")
                    # Heartbeat/token consumer may only finish after graph stop.
                    raise ExecutionNeedsRecovery("Execution observer stopped before the graph.")
            if cancel_task in done:
                requested = await finish_observer(cancel_task, run_id=run_id, stage="cancel_watch_failed")
                if requested:
                    if effects.may_have_submitted:
                        raise ExecutorOutcomeUnknown("Cancellation raced with Executor submission; reconcile before releasing the session")
                    raise RunService.CancellationRequested
            return await graph_task
        finally:
            await protected_cleanup(cleanup())

    @staticmethod
    @timed("run.lock_final_rows")
    async def _lock_run_and_task(
        db: AsyncSession,
        run_id: UUID,
    ) -> tuple[AgentRunModel, TaskModel | None]:
        """완료와 취소가 서로 덮어쓰지 않도록 같은 transaction에서 행을 잠급니다."""
        run = await db.scalar(
            select(AgentRunModel)
            .where(AgentRunModel.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None:
            raise RuntimeError(f"Run disappeared during execution: {run_id}")
        task = await db.scalar(
            select(TaskModel)
            .where(TaskModel.task_id == run.task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is not None and task.recovery_required:
            raise ExecutionNeedsRecovery("Task requires recovery; late completion is rejected.")
        TaskService.assert_execution_owner(run, task)
        return run, task

    @staticmethod
    def _finalize_canceled(
        run: AgentRunModel,
        task: TaskModel | None,
    ) -> None:
        """실제 실행 중단을 확인한 뒤 Run/Task를 canceled로 확정하고 잠금을 풉니다."""
        RunService._finish_run(run, AgentRunStatus.CANCELED)
        if task is not None:
            TaskService.transition(task, TaskStatus.CANCELED)

    @staticmethod
    async def _session(db: AsyncSession, user_id: UUID, session_id: UUID):
        session = await SessionRepository.get_active_by_user(db, user_id=user_id, session_id=session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        return session

    @staticmethod
    async def _interrupted_run(db: AsyncSession, session_id: UUID, resume_run_id: UUID | None) -> AgentRunModel:
        conditions = [AgentRunModel.session_id == session_id]
        if resume_run_id is not None:
            conditions.append(AgentRunModel.run_id == resume_run_id)
        else:
            conditions.append(AgentRunModel.status == AgentRunStatus.INTERRUPTED)
        run = await db.scalar(select(AgentRunModel).where(*conditions).order_by(AgentRunModel.created_at.desc()))
        if run is None or run.status != AgentRunStatus.INTERRUPTED:
            raise HTTPException(status_code=409, detail="No interrupted run exists to resume in this session.")
        return run

    @staticmethod
    def _finish_run(run: AgentRunModel, status: AgentRunStatus, *, failure: dict | None = None, interrupt=None) -> None:
        """AgentRun 상태는 실행 기록만 변경하며 Task 잠금은 TaskService가 처리합니다."""
        run.status = status
        run.failure = failure
        run.interrupt = interrupt
        if status in RunService.TERMINAL_STATUSES:
            run.completed_at = utc_now()

    @staticmethod
    async def _append_error_system_message(
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

    @staticmethod
    def select_model(name):
        from service_runtime.model_selection import current_catalog, ModelSelectionError
        try:
            return current_catalog().select(name).model_dump()
        except ModelSelectionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @staticmethod
    def validate_model(reference):
        from service_runtime.model_selection import current_catalog, ModelSelectionError
        try:
            current_catalog().resolve(reference)
        except ModelSelectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @staticmethod
    def request_digest(payload: RunCreate) -> str:
        data = payload.model_dump(mode="json")
        if data["main_model_name"] is None:
            del data["main_model_name"]  # Preserve idempotency hashes for old clients.
        serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def validate_replay(previous: AgentRunModel, payload: RunCreate) -> None:
        digest = (previous.metadata_json or {}).get("_request_digest")
        if digest is None:
            # Pre-migration resumes used a different public contract. Do not
            # guess that an old key belongs to a newly tokenized command.
            metadata = {k: v for k, v in (previous.metadata_json or {}).items()
                        if k not in {"checkpoint_run_id", "requested_by_user_id", "_request_digest"}}
            original = RunCreate(input=previous.input_json, command=previous.command_json,
                                 metadata=metadata, multitask_strategy=previous.multitask_strategy,
                                 stream_mode=previous.stream_mode, stream_resumable=previous.stream_resumable,
                                 on_disconnect=previous.on_disconnect)
            matches = previous.command_json is None and RunService.request_digest(original) == RunService.request_digest(payload)
        else:
            matches = digest == RunService.request_digest(payload)
        if not matches:
            raise HTTPException(status_code=409, detail="Idempotency-Key was already used for a different request.")

    @staticmethod
    async def create(
        db: AsyncSession,
        user_id: UUID,
        session_id: UUID,
        payload: RunCreate,
        key: str,
        *,
        _execute_existing: bool = False,
    ) -> AgentRunModel:
        """Run을 durable queue에 넣습니다.

        일반 API 호출은 pending Run을 commit하고 즉시 반환합니다. Router lifespan Worker만
        ``_execute_existing=True``로 이미 점유한 Run을 실제 실행합니다.
        """
        session = (await RunService._session(db, user_id, session_id) if _execute_existing
                   else await lifecycle.lock_session(db, user_id, session_id))
        execution_claim = current_execution_claim.get()
        if _execute_existing and execution_claim is None:
            raise ExecutionNeedsRecovery("Execution requires an immutable Worker claim.")
        previous = await db.scalar(select(AgentRunModel).where(
            AgentRunModel.session_id == session_id, AgentRunModel.idempotency_key == key
        ))
        if previous is not None:
            if not _execute_existing:
                RunService.validate_replay(previous, payload)
                return previous
            run = previous
            task = await db.get(TaskModel, run.task_id)
            if task is None:
                raise RuntimeError(f"Queued Run has no Task: {run.run_id}")
            if task.recovery_required:
                raise ExecutionNeedsRecovery("Task requires recovery before execution.")
            run, task = await RunService._lock_run_and_task(db, run.run_id)
            origin = None
            if payload.command is not None:
                resume_id = payload.metadata.get("resume_run_id") or payload.metadata.get("checkpoint_run_id")
                origin = await RunService._interrupted_run(
                    db, session_id, UUID(str(resume_id)) if resume_id else None
                )
        else:
            if _execute_existing:
                raise ExecutionNeedsRecovery("Claimed Run no longer exists; do not enqueue a replacement.")
            owner = "queue:unclaimed"
            origin: AgentRunModel | None = None
            try:
                from api_service.models.common.session_execution_model import SessionExecutionModel
                recovering = await db.scalar(select(SessionExecutionModel.session_id).where(
                    SessionExecutionModel.session_id == session_id,
                    SessionExecutionModel.recovery_required.is_(True),
                ))
                if recovering is not None:
                    raise HTTPException(status_code=409, detail="Session execution requires recovery.")
                if payload.command is None:
                    unfinished = await db.scalar(select(TaskModel.task_id).where(
                        TaskModel.session_id == session_id,
                        TaskModel.status.not_in(TaskService.TERMINAL_STATUSES),
                    ).limit(1))
                    if unfinished is not None:
                        raise HTTPException(status_code=409, detail="Session has an unfinished task; resume its requested input instead.")
                    model_selection = RunService.select_model(payload.main_model_name)
                    # Queue 대기 중에도 동일 Session의 두 분석 요청이 들어오지 못하게 Task를 선점합니다.
                    task = TaskService.create_model(
                        session_id=session_id, idempotency_key=key, owner=owner
                    )
                    task.status = TaskStatus.PENDING
                    db.add(task)
                    await db.flush()
                else:
                    resume_id = payload.metadata.get("resume_run_id") or payload.metadata.get("checkpoint_run_id")
                    origin = await RunService._interrupted_run(
                        db, session_id, UUID(str(resume_id)) if resume_id else None
                    )
                    latest_id = await db.scalar(select(AgentRunModel.run_id).where(
                        AgentRunModel.public_run_id == origin.public_run_id
                    ).order_by(AgentRunModel.created_at.desc(), AgentRunModel.run_id.desc()).limit(1))
                    if latest_id != origin.run_id:
                        raise HTTPException(status_code=409, detail="Resume target is no longer the current interrupt.")
                    if any(item.get("kind") == "EXECUTOR_EVENT" for item in (origin.interrupt or []) if isinstance(item, dict)):
                        raise HTTPException(status_code=409, detail="Session is waiting for Executor; user resume is not allowed.")
                    conflicting = select(TaskModel.task_id).where(
                        TaskModel.session_id == session_id,
                        TaskModel.status.not_in(TaskService.TERMINAL_STATUSES),
                    )
                    if origin.task_id is not None:
                        conflicting = conflicting.where(TaskModel.task_id != origin.task_id)
                    if await db.scalar(conflicting.limit(1)) is not None:
                        raise HTTPException(status_code=409, detail="Another unfinished task owns this session.")
                    root = await db.get(AgentRunModel, origin.public_run_id)
                    model_selection = (root.metadata_json or {}).get("_model_selection")
                    RunService.validate_model(model_selection)
                    if origin.task_id is None:
                        # FAQ 등 Task 없는 checkpoint 재개도 durable queue를 거칩니다.
                        task = TaskService.create_model(
                            session_id=session_id, idempotency_key=key, owner=owner
                        )
                        task.status = TaskStatus.PENDING
                        task.checkpoint_run_id = origin.checkpoint_run_id or origin.run_id
                        db.add(task)
                        await db.flush()
                    else:
                        task = await db.scalar(
                            select(TaskModel).where(TaskModel.task_id == origin.task_id).with_for_update()
                        )
                        if task is None or task.status != TaskStatus.WAITING_INPUT:
                            raise HTTPException(status_code=409, detail="Task is not waiting for input.")
                        if task.cancel_requested_at is not None:
                            raise HTTPException(status_code=409, detail="Task cancellation has been requested.")
                        # 재개 요청도 먼저 pending으로 원자 전환하고 Worker가 실제 lease를 획득합니다.
                        task.status = TaskStatus.PENDING
                        task.lock_owner = owner
                        task.lock_token = None
                        task.heartbeat_at = None
                        task.lease_expires_at = None
            except IntegrityError as exc:
                await db.rollback()
                duplicate = await db.scalar(select(AgentRunModel).where(
                    AgentRunModel.session_id == session_id, AgentRunModel.idempotency_key == key
                ))
                if duplicate is not None:
                    RunService.validate_replay(duplicate, payload)
                    return duplicate
                raise HTTPException(status_code=409, detail="An active task already exists for this session.") from exc

            checkpoint_run_id = task.checkpoint_run_id or (origin.checkpoint_run_id if origin else None)
            metadata = dict(payload.metadata)
            # Internal recovery controls are never accepted from client metadata.
            for field in ("_resume_target", "_resume_started", "_checkpoint_interrupt_id"):
                metadata.pop(field, None)
            if origin is not None:
                metadata["_resume_target"] = (origin.metadata_json or {}).get("_checkpoint_interrupt_id")
                metadata["_resume_started"] = False
            metadata["_model_selection"] = model_selection
            metadata["_request_digest"] = RunService.request_digest(payload)
            metadata["requested_by_user_id"] = str(user_id)
            if checkpoint_run_id is not None:
                metadata["checkpoint_run_id"] = str(checkpoint_run_id)
            invocation_id = uuid4()
            run = AgentRunModel(
                run_id=invocation_id,
                public_run_id=origin.public_run_id if origin else invocation_id,
                session_id=session_id, task_id=task.task_id,
                status=AgentRunStatus.PENDING,
                input_json=payload.input.model_dump(mode="json") if payload.input else None,
                command_json=payload.command, metadata_json=metadata,
                idempotency_key=key, multitask_strategy=payload.multitask_strategy,
                stream_mode=payload.stream_mode, stream_resumable=payload.stream_resumable,
                on_disconnect=payload.on_disconnect,
            )
            db.add(run)
            await db.flush()
            # 화면/API coordinator가 먼저 저장한 원본 User Message를 Run과 분석 Task에 연결한다.
            # Message CRUD는 자기 테이블만 처리하고, 관계 연결은 실행 경계인 RunService가 담당한다.
            trigger_message_id = metadata.get("trigger_message_id")
            if trigger_message_id is not None:
                try:
                    trigger_message_uuid = UUID(str(trigger_message_id))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="Invalid trigger_message_id.") from exc
                trigger_message = await db.scalar(
                    select(MessageModel).where(
                        MessageModel.message_id == trigger_message_uuid,
                        MessageModel.session_id == session_id,
                    )
                )
                if trigger_message is None:
                    raise HTTPException(status_code=422, detail="Trigger message does not belong to this session.")
                run.trigger_message_id = trigger_message_uuid
                task.trigger_message_id = trigger_message_uuid
            if task.root_run_id is None:
                task.root_run_id = run.public_run_id
            if task.checkpoint_run_id is None:
                task.checkpoint_run_id = run.run_id
                run.metadata_json = {**(run.metadata_json or {}), "checkpoint_run_id": str(run.run_id)}
            # Worker가 pending Run을 보기 전에 queued event까지 같은 transaction에서 완성한다.
            # commit 뒤 이벤트를 추가하면 Worker의 Task lease UPDATE와 lock 순서가 엇갈려
            # PostgreSQL deadlock이 발생할 수 있다.
            await TaskEventService.append(
                db,
                task_id=task.task_id,
                run_id=run.run_id,
                event_type="task.queued",
                payload={"status": AgentRunStatus.PENDING.value},
                commit=False,
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                duplicate = await db.scalar(select(AgentRunModel).where(
                    AgentRunModel.session_id == session_id, AgentRunModel.idempotency_key == key
                ))
                if duplicate is not None:
                    RunService.validate_replay(duplicate, payload)
                    return duplicate
                raise HTTPException(status_code=409, detail="An active task already exists for this session.") from exc
            await db.refresh(run)
            await db.refresh(task)
            return run

        await db.refresh(run)
        await db.refresh(task)
        # rollback은 ORM 속성을 expire하므로 취소/오류 처리에 쓸 ID는 평범한 값으로 보존합니다.
        execution_model_selection = (run.metadata_json or {}).get("_model_selection")
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

        token_events = LLMTokenEventBuffer(task_id=execution_task_id, run_id=execution_run_id)
        token_events.start()
        try:
            try:
                async with TaskService.lease_heartbeat(execution_claim.task_id, execution_claim.lock_token, run_id=execution_run_id) as heartbeat:
                    from service_runtime.model_selection import current_catalog
                    current_catalog().resolve(execution_model_selection)
                    if payload.command is not None:
                        state = await RunService._run_cancellable(
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
                            raise HTTPException(status_code=422, detail="Run input is required when command is omitted.")
                        state = await RunService._run_cancellable(
                            execution_run_id,
                            ainvoke_user_turn(
                                user_id=user_id, project_id=execution_project_id,
                                session_id=session_id, run_id=execution_run_id,
                                user_request=user_request_from_messages(payload.input.messages),
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
            if payload.command is not None:
                try:
                    return await RunService._finalize_state(db, execution_run_id, user_id, state)
                except ExecutionNeedsRecovery:
                    raise
                except Exception as exc:
                    raise ResumeProjectionError("Durable user resume needs final state projection") from exc
        except UserResumeNeedsRecovery as exc:
            # The graph and observers have stopped. Quarantine this Task only;
            # existing ownership-uncertain failures still poison the process below.
            return await RunService._require_resume_recovery(db, execution_run_id, str(exc))
        except (ExecutionNeedsRecovery, asyncio.CancelledError):
            # No retry/terminal transition without confirmed execution ownership.
            # The independent recorder remains able to mark a stuck live graph.
            execution_health.fail(execution_run_id, "execution_interrupted_or_uncertain")
            await db.rollback()
            await TaskService.require_recovery(
                db, run_id=execution_run_id, reason=execution_health.faults[str(execution_run_id)]
            )
            raise
        except RunService.CancellationRequested:
            # 취소 API가 기록한 요청을 다시 읽은 후에만 terminal 상태와 lock을 변경합니다.
            await db.rollback()
            run, task = await RunService._lock_run_and_task(db, execution_run_id)
            RunService._finalize_canceled(run, task)
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
                "code": ("RESUME_PROJECTION_FAILED" if isinstance(exc, ResumeProjectionError)
                         else "RUN_MODEL_UNAVAILABLE" if isinstance(exc, ModelSelectionError) else "AGENT_RUN_FAILED"),
                "stage": "state_projection" if isinstance(exc, ResumeProjectionError) else "graph_execution",
                "exception_type": f"{type(exc).__module__}.{type(exc).__name__}",
                "message": str(exc),
                "error_id": error_id,
            }
            await db.rollback()
            # A successful final commit releases the Task lease. Read its outcome
            # before requiring that old lease again; session ownership is still held.
            if payload.command is not None:
                committed = await db.get(AgentRunModel, execution_run_id, populate_existing=True)
                if committed is not None and (
                    committed.status in RunService.TERMINAL_STATUSES
                    or committed.status == AgentRunStatus.INTERRUPTED
                ):
                    return committed
            run, task = await RunService._lock_run_and_task(db, execution_run_id)
            if payload.command is not None and (
                run.attempt_count > max(0, settings.agent_worker_max_retries)
                or ((run.metadata_json or {}).get("_resume_started") and not RunService._is_retryable(exc))
            ):
                return await RunService._require_resume_recovery(
                    db, execution_run_id, "User resume cannot be safely retried or retry budget exhausted")
            if run.cancel_requested_at is not None:
                # 취소와 Graph 예외가 경쟁하면 사용자가 먼저 요청한 취소를 최종 상태로 보존합니다.
                RunService._finalize_canceled(run, task)
            elif (
                task is not None
                and RunService._is_retryable(exc)
                and run.attempt_count <= max(0, settings.agent_worker_max_retries)
            ):
                # Task는 terminal로 만들지 않아 동일 Session의 원자 잠금을 계속 유지합니다.
                delay = RunService._retry_delay_seconds(run.attempt_count)
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
                RunService._finish_run(run, AgentRunStatus.ERROR, failure=failure)
                if task is not None:
                    TaskService.transition(task, TaskStatus.ERROR, failure_reason=str(exc))
                # SSE task.error와 같은 오류를 messages에도 system 역할로 영속화합니다.
                await RunService._append_error_system_message(
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
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=503 if isinstance(exc, RuntimeError) else 502, detail=f"Agent graph failed: {exc}") from exc

        return await RunService._finalize_state(db, execution_run_id, user_id, state)

    @staticmethod
    async def _require_resume_recovery(db, run_id, reason):
        try:
            await db.rollback()
            if not await TaskService.require_recovery(db, run_id=run_id, reason=reason):
                raise ExecutionNeedsRecovery("Could not quarantine user resume")
            run = await db.get(AgentRunModel, run_id, populate_existing=True)
            if run is None:
                raise ExecutionNeedsRecovery("Quarantined user resume disappeared")
            return run
        except ExecutionNeedsRecovery:
            raise
        except Exception as exc:
            # Never release the owner if the durable Task guard was not verified.
            raise ExecutionNeedsRecovery("Could not verify user resume quarantine") from exc

    @staticmethod
    async def _finalize_state(db, execution_run_id, user_id, state):
        status = run_status_from_state(state)
        route = (state.get("routing_result") or {}).get("route")
        # 완료 직전 row lock을 획득해 동시에 들어온 cancel 요청과 최종 상태를 직렬화합니다.
        run, task = await RunService._lock_run_and_task(db, execution_run_id)
        boundaries = [getattr(item, "id", None) for item in state.get("__interrupt__", ())]
        run.metadata_json = {**(run.metadata_json or {}),
                             "_checkpoint_interrupt_id": boundaries[0] if len(boundaries) == 1 else None}
        if run.cancel_requested_at is not None:
            RunService._finalize_canceled(run, task)
            await TaskEventService.append_for_run(
                db, run_id=execution_run_id, event_type="task.canceled",
                payload={"status": "canceled"}, commit=False,
            )
            await db.commit()
            await db.refresh(run)
            return run
        RunService._finish_run(run, status, interrupt=interrupt_payload(state))
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

    @staticmethod
    async def attach_trigger_message(db: AsyncSession, *, run_id: UUID, message_id: UUID) -> None:
        run = await db.get(AgentRunModel, run_id)
        if run is not None and run.trigger_message_id is None:
            run.trigger_message_id = message_id
            if run.task_id is not None:
                await TaskService.attach_trigger(db, task_id=run.task_id, message_id=message_id)

    @staticmethod
    async def read(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID) -> AgentRunModel:
        await RunService._session(db, user_id, session_id)
        run = await db.scalar(select(AgentRunModel).where(
            AgentRunModel.run_id == run_id, AgentRunModel.session_id == session_id
        ))
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run

    @staticmethod
    async def cancel(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID, payload: RunCancel) -> AgentRunModel:
        await RunService._session(db, user_id, session_id)
        run = await db.scalar(
            select(AgentRunModel)
            .where(
                AgentRunModel.run_id == run_id,
                AgentRunModel.session_id == session_id,
            )
            .with_for_update()
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        if run.status in RunService.TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Run is already terminal.")
        if run.cancel_requested_at is not None:
            return run
        now = utc_now()
        # 실제 Graph가 멈추기 전에는 status와 Task lease를 active로 유지합니다.
        run.cancel_requested_at = now
        run.cancel_reason = payload.reason
        task = await db.scalar(
            select(TaskModel).where(TaskModel.task_id == run.task_id).with_for_update()
        )
        if task is not None:
            task.cancel_requested_at = now
        await db.commit()
        await db.refresh(run)
        return run

    @staticmethod
    async def cancel_task(
        db: AsyncSession, user_id: UUID, task_id: UUID, reason: str | None
    ) -> TaskModel:
        """전체 분석 Job을 취소합니다. waiting_input은 실행 coroutine이 없어 즉시 종료합니다."""
        task = await db.scalar(
            select(TaskModel)
            .join(SessionModel, SessionModel.session_id == TaskModel.session_id)
            .where(TaskModel.task_id == task_id, SessionModel.user_id == user_id)
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        latest_run = await db.scalar(
            select(AgentRunModel)
            .where(AgentRunModel.task_id == task_id)
            .order_by(AgentRunModel.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        # Same Run -> Task order as completion and recovery, avoiding an AB/BA
        # deadlock with a cancellation request during cleanup.
        task = await db.scalar(select(TaskModel).where(TaskModel.task_id == task_id)
                               .with_for_update().execution_options(populate_existing=True))
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        current_run_id = await db.scalar(select(AgentRunModel.run_id).where(AgentRunModel.task_id == task_id)
                                         .order_by(AgentRunModel.created_at.desc()).limit(1))
        if current_run_id != (latest_run.run_id if latest_run is not None else None):
            raise HTTPException(status_code=409, detail="Task execution changed; retry cancellation.")
        if task.status in TaskService.TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Task is already terminal.")
        if task.recovery_required:
            raise HTTPException(status_code=409, detail="Task requires recovery; termination has not been confirmed.")
        if latest_run is not None and task.status == TaskStatus.WAITING_INPUT and any(
            item.get("kind") == "EXECUTOR_EVENT" for item in (latest_run.interrupt or [])
        ):
            raise HTTPException(status_code=409, detail="Executor cancellation is not supported; wait for its result.")
        if task.status == TaskStatus.WAITING_INPUT or latest_run is None:
            TaskService.transition(task, TaskStatus.CANCELED)
            task.cancel_requested_at = utc_now()
            if latest_run is not None:
                latest_run.cancel_reason = reason
                latest_run.cancel_requested_at = task.cancel_requested_at
                await TaskEventService.append(
                    db, task_id=task_id, run_id=latest_run.run_id,
                    event_type="task.canceled",
                    payload={"status": TaskStatus.CANCELED.value, "reason": reason},
                    commit=False,
                )
            await db.commit()
            await db.refresh(task)
            return task

        now = utc_now()
        task.cancel_requested_at = now
        latest_run.cancel_requested_at = now
        latest_run.cancel_reason = reason
        await db.commit()
        await db.refresh(task)
        return task

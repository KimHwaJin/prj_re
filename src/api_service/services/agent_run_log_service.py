from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.models.common.agent_run_log_model import AgentRunLogModel
from api_service.models.common.task_event_model import TaskEventModel
from api_service.services.task_event_service import TaskEventService


class AgentRunLogService:
    """Append a log and its replayable event in one short transaction."""

    @staticmethod
    async def lock_run(db: AsyncSession, run_id: UUID) -> None:
        # Every log writer takes this before log/Task row locks. A batch can
        # otherwise deadlock with a single writer inserting a later log first.
        await db.execute(select(func.pg_advisory_xact_lock(
            func.hashtextextended(f"result-logs:{run_id}", 0),
        )))

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        run_id: UUID,
        event_key: str,
        agent_name: str | None,
        node: str,
        event: str,
        kind: str,
        payload: dict,
        commit: bool = True,
        batch=None,
    ) -> AgentRunLogModel:
        try:
            # Projection replays revisit earlier logs. A committed complete pair
            # needs only a read, with no extra writes, sequence allocation or locks.
            existing = (await db.execute(select(AgentRunLogModel, TaskEventModel.task_event_id)
                .outerjoin(TaskEventModel, TaskEventModel.agent_run_log_id == AgentRunLogModel.log_id)
                .where(AgentRunLogModel.run_id == run_id, AgentRunLogModel.event_key == event_key)
                .execution_options(populate_existing=True))).one_or_none()
            if existing is not None and existing[1] is not None:
                return existing[0]
            if batch is None:
                await AgentRunLogService.lock_run(db, run_id)
            else:
                batch.require_log_context(db, run_id)
            # Concurrent callers wait on this unique key without rolling back
            # the transaction on a duplicate. The first stored payload wins.
            log = await db.scalar(insert(AgentRunLogModel).values(
                run_id=run_id, event_key=event_key, agent_name=agent_name,
                node=node, event=event, kind=kind, payload=payload,
            ).on_conflict_do_nothing(constraint="uq_agent_run_logs_event").returning(AgentRunLogModel))
            linked = None
            if log is None:
                # A concurrent insert or legacy log won. Lock/re-read only this
                # path; a freshly returned INSERT already owns its new row.
                log = await db.scalar(select(AgentRunLogModel).where(
                    AgentRunLogModel.run_id == run_id,
                    AgentRunLogModel.event_key == event_key,
                ).with_for_update().execution_options(populate_existing=True))
                linked = await db.scalar(select(TaskEventModel.task_event_id).where(
                    TaskEventModel.agent_run_log_id == log.log_id,
                ))
            if linked is None:
                # Also repairs a legacy log whose corresponding event is missing.
                # Always use the persisted log, never a changed retry payload.
                await TaskEventService.append_for_run(
                    db, run_id=log.run_id, event_type="agent.event",
                    agent_run_log_id=log.log_id, commit=False,
                    payload={"agent_name": log.agent_name, "node": log.node,
                             "event": log.event, "kind": log.kind, "payload": log.payload},
                )
            # Log, event and Task sequence become visible together. A Run with
            # no associated Task retains the existing log-only behavior.
            if commit:
                await db.commit()
            # Production sessions retain loaded values across commit; RETURNING
            # includes generated IDs/timestamps. Support other session factories.
            if commit and db.sync_session.expire_on_commit:
                await db.refresh(log)
            return log
        except BaseException:
            await db.rollback()
            raise

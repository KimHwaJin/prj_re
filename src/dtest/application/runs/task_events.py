from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.infrastructure.database.models.task_event_model import (
    TaskEventModel,
)
from dtest.infrastructure.database.models.task_model import TaskModel
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel


class TaskEventService:
    """Task에 저장하는 durable event의 append와 공개 Run SSE 조회를 담당합니다."""

    @staticmethod
    async def append(
        db: AsyncSession,
        *,
        task_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict,
        commit: bool = True,
        agent_run_log_id: UUID | None = None,
    ) -> TaskEventModel | None:
        sequence = await db.scalar(
            update(TaskModel)
            .where(TaskModel.task_id == task_id)
            # UPDATE 자체의 row lock과 RETURNING으로 여러 producer의 번호를 원자 할당합니다.
            # ORM refresh를 하지 않으므로 같은 transaction의 미flush 상태 변경도 덮어쓰지 않습니다.
            .values(last_event_sequence=TaskModel.last_event_sequence + 1)
            .returning(TaskModel.last_event_sequence)
        )
        if sequence is None:
            # FAQ 등 routing 후 hard-delete된 임시 Task에는 SSE event를 남기지 않습니다.
            return None
        if (
            payload.get("schema_version") == 1
            and payload.get("type") == event_type
        ):
            payload = {**payload, "sequence": sequence}
        event = TaskEventModel(
            task_id=task_id,
            run_id=run_id,
            sequence=sequence,
            agent_run_log_id=agent_run_log_id,
            event_type=event_type,
            payload=payload,
        )
        db.add(event)
        if commit:
            await db.commit()
            await db.refresh(event)
        else:
            await db.flush()
        return event

    @staticmethod
    async def append_for_run(
        db: AsyncSession,
        *,
        run_id: UUID,
        event_type: str,
        payload: dict,
        commit: bool = True,
        agent_run_log_id: UUID | None = None,
    ) -> TaskEventModel | None:
        task_id = await db.scalar(
            select(AgentRunModel.task_id).where(AgentRunModel.run_id == run_id)
        )
        if task_id is None:
            return None
        return await TaskEventService.append(
            db,
            task_id=task_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            commit=commit,
            agent_run_log_id=agent_run_log_id,
        )

    @staticmethod
    async def list_after_public_run(
        db: AsyncSession, *, run_id: UUID, sequence: int, limit: int
    ):
        return list(
            (
                await db.scalars(
                    select(TaskEventModel)
                    .join(
                        AgentRunModel,
                        AgentRunModel.run_id == TaskEventModel.run_id,
                    )
                    .where(
                        AgentRunModel.public_run_id == run_id,
                        TaskEventModel.sequence > sequence,
                    )
                    .order_by(TaskEventModel.sequence)
                    .limit(limit)
                )
            ).all()
        )

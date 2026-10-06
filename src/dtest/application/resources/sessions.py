from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from dtest.application.resources import lifecycle
from dtest.application.resources.cascade import (
    soft_delete_messages_for_sessions,
    soft_delete_sessions,
)
from dtest.contracts.enums import DeleteYN
from dtest.contracts.errors import ApplicationError
from dtest.contracts.resources.session_schema import (
    SessionCreate,
    SessionDeleteResult,
    SessionUpdate,
)
from dtest.contracts.session_settings import (
    SessionSettings,
    resolve_session_settings,
)
from dtest.contracts.values import normalize_name
from dtest.infrastructure.database.models.session_model import SessionModel
from dtest.infrastructure.database.repositories.project_repository import (
    ProjectRepository,
)
from dtest.infrastructure.database.repositories.session_repository import (
    SessionRepository,
)


class SessionService:
    @staticmethod
    async def _require_project(
        db: AsyncSession, user_id: UUID, project_id: UUID
    ):
        project = await ProjectRepository.get_owned_active(
            db,
            user_id=user_id,
            project_id=project_id,
        )
        if project is None:
            raise ApplicationError(
                status_code=404, detail="Project를 찾을 수 없습니다."
            )
        return project

    @staticmethod
    async def create_internal(
        db: AsyncSession,
        *,
        user_id: UUID,
        project_id: UUID,
        session_name: str | None = None,
        settings: SessionSettings | dict | None = None,
    ) -> SessionModel:
        from dtest.settings.loader import get_settings

        agent = get_settings().agent
        try:
            resolved_settings = resolve_session_settings(
                settings,
                default_profile=agent.executor_runtime_profile,
                allowed_profiles=agent.executor_runtime_profiles
                or (agent.executor_runtime_profile,),
            )
        except ValueError as exc:
            raise ApplicationError(
                422, "Invalid or unsupported session kernel_profile/settings."
            ) from exc
        await lifecycle.lock_projects(db, user_id, [project_id])

        name = normalize_name(session_name or "") or "새 대화"
        session = SessionModel(
            user_id=user_id,
            project_id=project_id,
            session_name=name,
            settings=resolved_settings,
            delete_yn=DeleteYN.N,
        )
        db.add(session)
        await db.flush()
        return session

    @staticmethod
    async def create(
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
        payload: SessionCreate,
    ) -> SessionModel:
        session = await SessionService.create_internal(
            db,
            user_id=user_id,
            project_id=project_id,
            session_name=payload.session_name,
            settings=payload.settings,
        )
        await db.commit()
        # The public response now uses a fresh scalar activity snapshot. Avoid
        # another full Session refresh with the normal non-expiring factory.
        if db.sync_session.expire_on_commit:
            await db.refresh(session)
        return session

    @staticmethod
    async def update(
        db: AsyncSession,
        user_id: UUID,
        current_project_id: UUID,
        session_id: UUID,
        payload: SessionUpdate,
    ) -> SessionModel:
        await lifecycle.lock_session(
            db, user_id, session_id, expected_project_id=current_project_id
        )
        session = await SessionRepository.get_active(
            db,
            user_id=user_id,
            project_id=current_project_id,
            session_id=session_id,
            for_update=True,
        )
        if session is None:
            raise ApplicationError(
                status_code=404, detail="Session을 찾을 수 없습니다."
            )

        if payload.session_name is not None:
            name = normalize_name(payload.session_name)
            if not name:
                raise ApplicationError(
                    status_code=422,
                    detail="session_name은 공백일 수 없습니다.",
                )
            session.session_name = name

        await db.commit()
        # The public response now uses a fresh scalar activity snapshot. Avoid
        # another full Session refresh with the normal non-expiring factory.
        if db.sync_session.expire_on_commit:
            await db.refresh(session)
        return session

    @staticmethod
    async def delete(
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
        session_id: UUID,
    ) -> SessionDeleteResult:
        await lifecycle.lock_session(
            db, user_id, session_id, expected_project_id=project_id
        )
        await lifecycle.require_idle(db, [session_id], resource="Session")
        session = await SessionRepository.get_active(
            db,
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            for_update=True,
        )
        if session is None:
            raise ApplicationError(
                status_code=404, detail="Session을 찾을 수 없습니다."
            )

        deleted_message_count = await soft_delete_messages_for_sessions(
            db, [session_id]
        )
        await soft_delete_sessions(db, [session_id])
        await db.commit()

        return SessionDeleteResult(
            session_id=session_id,
            deleted_message_count=deleted_message_count,
            detail="Session과 하위 Message를 삭제했습니다.",
        )

from uuid import UUID

from dtest.contracts.errors import ApplicationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.contracts.enums import DeleteYN
from dtest.infrastructure.database.models.project_model import (
    ProjectModel
)
from dtest.infrastructure.database.models.session_model import SessionModel
from dtest.infrastructure.database.repositories.project_repository import ProjectRepository
from dtest.infrastructure.database.repositories.user_repository import UserRepository
from dtest.contracts.resources.project_schema import (
    ProjectCreate,
    ProjectUpdate,
)
from dtest.application.resources.cascade import (
    soft_delete_messages_for_sessions,
    soft_delete_sessions
)
from dtest.contracts.values import normalize_name, utc_now
from dtest.application.resources import lifecycle


class ProjectService:
    @staticmethod
    async def _require_project(db: AsyncSession, user_id: UUID, project_id: UUID):
        project = await ProjectRepository.get_owned_active(
            db,
            user_id=user_id,
            project_id=project_id,
        )
        if project is None:
            raise ApplicationError(status_code=404, detail="Project를 찾을 수 없습니다.")
        return project

    @staticmethod
    async def create(
        db: AsyncSession,
        user_id: UUID,
        payload: ProjectCreate,
    ) -> ProjectModel:
        if await UserRepository.get_active(db, user_id, for_share=True) is None:
            raise ApplicationError(404, "User not found.")

        project_name = normalize_name(payload.project_name)
        if not project_name:
            # 요구사항: 공란은 default로 보지만 default가 이미 있으므로 생성하지 않음.
            raise ApplicationError(
                status_code=409,
                detail="project_name 공란은 default Project로 간주되며 이미 존재합니다.",
            )

        if await ProjectRepository.get_active_by_name(
            db,
            user_id=user_id,
            project_name=project_name,
        ):
            raise ApplicationError(status_code=409, detail="동일한 Project 이름이 이미 존재합니다.")

        project = ProjectModel(
            user_id=user_id,
            project_name=project_name,
            system_prompt=payload.system_prompt,
            prompt_version=1,
            is_default=False,
            delete_yn=DeleteYN.N,
        )
        db.add(project)
        await db.flush()


        await db.commit()
        await db.refresh(project)
        return project

    @staticmethod
    async def read(
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
    ) -> ProjectModel:
        # Children are exposed through their own paginated endpoints.
        return await ProjectService._require_project(db, user_id, project_id)

    @staticmethod
    async def update(
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
        payload: ProjectUpdate,
    ) -> ProjectModel:
        await lifecycle.lock_projects(db, user_id, [project_id])
        project = await db.scalar(select(ProjectModel).where(ProjectModel.project_id == project_id)
                                  .with_for_update().execution_options(populate_existing=True))
        if project.is_default and payload.project_name is not None:
            raise ApplicationError(status_code=409, detail="default Project 이름은 변경할 수 없습니다.")

        if payload.project_name is not None:
            project_name = normalize_name(payload.project_name)
            if not project_name:
                raise ApplicationError(status_code=422, detail="project_name cannot be blank.")

            duplicate = await ProjectRepository.get_active_by_name(
                db,
                user_id=user_id,
                project_name=project_name,
            )
            if duplicate is not None and duplicate.project_id != project_id:
                raise ApplicationError(status_code=409, detail="A project with this name already exists.")
            project.project_name = project_name

        if payload.system_prompt is not None and project.system_prompt != payload.system_prompt:
            project.system_prompt = payload.system_prompt
            project.prompt_version += 1
        await db.commit()
        await db.refresh(project)
        return project

    @staticmethod
    async def delete(
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
    ) -> None:
        projects = await lifecycle.lock_projects(db, user_id, [project_id], exclusive=True)
        project = projects[project_id]
        if project.is_default:
            raise ApplicationError(409, "The default Project cannot be deleted.")
        # Include old soft-deleted sessions too: old faulty deletions must not
        # hide a still-running job from the project-wide guard.
        all_sessions = select(SessionModel.session_id).where(SessionModel.project_id == project_id)
        await lifecycle.require_idle(db, all_sessions, resource="Project")

        session_ids = list(
            (
                await db.scalars(
                    select(SessionModel.session_id).where(
                        SessionModel.project_id == project_id,
                        SessionModel.user_id == user_id,
                        SessionModel.delete_yn == DeleteYN.N,
                    )
                )
            ).all()
        )

        await soft_delete_messages_for_sessions(db, session_ids)
        await soft_delete_sessions(db, session_ids)

        project.delete_yn = DeleteYN.Y
        project.deleted_at = utc_now()

        await db.commit()

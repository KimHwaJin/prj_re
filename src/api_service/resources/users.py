"""User lifecycle with atomic default project creation and serialized role changes."""
from fastapi import HTTPException
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.dependencies import Actor
from api_service.models.enums import DeleteYN, UserRole
from api_service.resources.identity import normalize_user_id
from api_service.models.message_model import MessageModel
from api_service.models.project_model import ProjectModel
from api_service.models.session_model import SessionModel
from api_service.models.user_model import UserModel
from api_service.repositories.project_repository import ProjectRepository
from api_service.repositories.user_repository import UserRepository
from api_service.schemas.user_schema import UserCreate, UserRead, UserUpdate
from api_service.utils import utc_now
from api_service.resources import lifecycle

# User management is infrequent. Serialize it across processes, including initial
# bootstrap, to make last-admin checks safe under concurrent transactions.
USER_ADMIN_LOCK = 178521094


class UserService:
    @staticmethod
    async def _management_lock(db: AsyncSession):
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": USER_ADMIN_LOCK})

    @staticmethod
    async def _require_admin_locked(db: AsyncSession, actor: Actor):
        await UserService._management_lock(db)
        # Re-read after waiting: the request's original Actor can be stale.
        user = await UserRepository.get_by_public_id(db, actor.public_user_id, active_only=True, for_update=True)
        if user is None:
            raise HTTPException(401, "A registered, active user is required.")
        if user.role != UserRole.ADMIN:
            raise HTTPException(403, "Administrator role is required.")

    @staticmethod
    async def _target(db, public_id, *, for_update=False, active_only=True):
        try:
            public_id = normalize_user_id(public_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        user = await UserRepository.get_by_public_id(db, public_id, active_only=active_only, for_update=for_update)
        if user is None:
            raise HTTPException(404, "User not found.")
        return user

    @staticmethod
    async def _resource(db, user) -> UserRead:
        result = UserRead.model_validate(user)
        project = await ProjectRepository.get_default(db, user_id=user.user_id)
        return result.model_copy(update={"default_project_id": project.project_id if project else None})

    @staticmethod
    async def _insert(db, payload: UserCreate):
        # IDs remain reserved after soft deletion.
        if await UserRepository.get_by_public_id(db, payload.user_id) is not None:
            raise HTTPException(409, "user_id is already registered.")
        user = UserModel(public_user_id=payload.user_id, user_name=payload.user_name,
                         role=payload.role, delete_yn=DeleteYN.N)
        db.add(user)
        await db.flush()
        project = ProjectModel(user_id=user.user_id, project_name="default", system_prompt="",
                               prompt_version=1, is_default=True, delete_yn=DeleteYN.N)
        db.add(project)
        await db.flush()
        return user

    @staticmethod
    async def _finish(db, user):
        await db.flush()
        await db.refresh(user)
        result = await UserService._resource(db, user)
        await db.commit()
        return result

    @staticmethod
    async def create(db: AsyncSession, actor: Actor, payload: UserCreate) -> UserRead:
        await UserService._require_admin_locked(db, actor)
        try:
            user = await UserService._insert(db, payload)
            return await UserService._finish(db, user)
        except IntegrityError as exc:
            await db.rollback()
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise HTTPException(409, "user_id is already registered.") from None
            raise

    @staticmethod
    async def read(db: AsyncSession, actor: Actor, public_id: str) -> UserRead:
        try:
            public_id = normalize_user_id(public_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if actor.role != UserRole.ADMIN and actor.public_user_id != public_id:
            raise HTTPException(404, "User not found.")
        # Only administrators may inspect soft-deleted profiles. Mutation callers
        # keep _target's active_only=True and SSO never reactivates them here.
        return await UserService._resource(db, await UserService._target(
            db, public_id, active_only=actor.role != UserRole.ADMIN,
        ))

    @staticmethod
    async def _protect_last_admin(db, target):
        if target.role == UserRole.ADMIN:
            count = await db.scalar(select(func.count()).select_from(UserModel).where(
                UserModel.role == UserRole.ADMIN, UserModel.delete_yn == DeleteYN.N))
            if count <= 1:
                raise HTTPException(409, "The last active administrator cannot be removed or demoted.")

    @staticmethod
    async def update(db: AsyncSession, actor: Actor, public_id: str, payload: UserUpdate) -> UserRead:
        await UserService._require_admin_locked(db, actor)
        target = await UserService._target(db, public_id, for_update=True)
        if payload.role is not None and payload.role != target.role:
            await UserService._protect_last_admin(db, target)
            target.role = payload.role
        if payload.user_name is not None:
            target.user_name = payload.user_name
        return await UserService._finish(db, target)

    @staticmethod
    async def delete(db: AsyncSession, actor: Actor, public_id: str) -> None:
        await UserService._require_admin_locked(db, actor)
        # Exclusive user lock orders this transaction after all admitted business
        # requests (FOR SHARE), then prevents new requests until deletion commits.
        target = await UserService._target(db, public_id, for_update=True)
        await UserService._protect_last_admin(db, target)
        projects = select(ProjectModel.project_id).where(ProjectModel.user_id == target.user_id)
        sessions = select(SessionModel.session_id).where(
            or_(SessionModel.user_id == target.user_id, SessionModel.project_id.in_(projects)))
        await lifecycle.require_idle(db, sessions, resource="User")
        now = utc_now()
        await db.execute(update(MessageModel).where(
            MessageModel.session_id.in_(sessions), MessageModel.delete_yn == DeleteYN.N
        ).values(delete_yn=DeleteYN.Y, deleted_at=now))
        await db.execute(update(SessionModel).where(
            SessionModel.session_id.in_(sessions), SessionModel.delete_yn == DeleteYN.N
        ).values(delete_yn=DeleteYN.Y, deleted_at=now, current_leaf_message_id=None))
        await db.execute(update(ProjectModel).where(
            ProjectModel.user_id == target.user_id, ProjectModel.delete_yn == DeleteYN.N
        ).values(delete_yn=DeleteYN.Y, deleted_at=now))
        target.delete_yn, target.deleted_at = DeleteYN.Y, now
        await db.commit()

    @staticmethod
    async def bootstrap_admin(db: AsyncSession, payload: UserCreate) -> UserRead:
        if payload.role != UserRole.ADMIN:
            raise ValueError("Bootstrap requires role=admin")
        await UserService._management_lock(db)
        existing = await UserRepository.get_by_public_id(db, payload.user_id, for_update=True)
        if existing is not None:
            if existing.delete_yn != DeleteYN.N or existing.role != UserRole.ADMIN:
                raise HTTPException(409, "Bootstrap cannot promote or reactivate an existing account.")
            result = await UserService._resource(db, existing)
            await db.commit()
            return result  # Idempotent: do not change the existing name or role.
        if await db.scalar(select(UserModel.user_id).where(
            UserModel.role == UserRole.ADMIN, UserModel.delete_yn == DeleteYN.N).limit(1)):
            raise HTTPException(409, "An administrator already exists; use the administrator API.")
        return await UserService._finish(db, await UserService._insert(db, payload))

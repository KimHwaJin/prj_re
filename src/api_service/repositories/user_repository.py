from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.models.enums import DeleteYN
from api_service.models.user_model import UserModel


class UserRepository:
    @staticmethod
    async def get_active(db: AsyncSession, user_id: UUID, *, for_share: bool = False) -> UserModel | None:
        query = select(UserModel).where(
                UserModel.user_id == user_id,
                UserModel.delete_yn == DeleteYN.N,
            )
        if for_share:
            query = query.with_for_update(read=True)
        return await db.scalar(query.execution_options(populate_existing=True))

    @staticmethod
    async def get_by_public_id(db: AsyncSession, public_id: str, *, active_only=False, for_update=False, for_share=False):
        query = select(UserModel).where(UserModel.public_user_id == public_id)
        if active_only:
            query = query.where(UserModel.delete_yn == DeleteYN.N)
        if for_update:
            query = query.with_for_update()
        elif for_share:
            query = query.with_for_update(read=True)
        return await db.scalar(query.execution_options(populate_existing=True))

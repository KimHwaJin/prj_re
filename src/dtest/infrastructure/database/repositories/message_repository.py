from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.contracts.enums import DeleteYN
from dtest.infrastructure.database.models import MessageModel, SessionModel


class MessageRepository:
    @staticmethod
    async def get_owned_active(
        db: AsyncSession,
        *,
        user_id: UUID,
        message_id: UUID,
    ) -> MessageModel | None:
        return await db.scalar(
            select(MessageModel)
            .join(
                SessionModel,
                SessionModel.session_id == MessageModel.session_id,
            )
            .where(
                MessageModel.message_id == message_id,
                MessageModel.delete_yn == DeleteYN.N,
                SessionModel.user_id == user_id,
                SessionModel.delete_yn == DeleteYN.N,
            )
        )

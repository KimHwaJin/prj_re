"""Messages visible to the owning user/session."""
from sqlalchemy import select
from dtest.contracts.enums import DeleteYN
from dtest.infrastructure.database.models import MessageModel, SessionModel
from dtest.infrastructure.database.pagination import fetch_page
from dtest.contracts.resources.api_schema import MessageResource

async def list_messages(db, user_id, session_id, params):
    items, page = await fetch_page(
        db,
        # sessions.current_leaf_message_id도 messages를 참조하므로 자동 join은
        # FK 경로가 모호합니다. Message 소속 Session FK를 명시합니다.
        select(MessageModel).join(
            SessionModel,
            SessionModel.session_id == MessageModel.session_id,
        ).where(
            MessageModel.session_id == session_id,
            MessageModel.delete_yn == DeleteYN.N,
            SessionModel.user_id == user_id,
            SessionModel.delete_yn == DeleteYN.N,
        ),
        model=MessageModel,
        id_name="message_id",
        params=params,
    )
    return {"items": [MessageResource.model_validate(item) for item in items], "page": page}

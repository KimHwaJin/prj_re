from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Header, HTTPException, Response, status

from dtest.api_service.http.dependencies import (
    CurrentUserId,
    DBSession,
)
from dtest.api_service.http.pagination import ListQuery
from dtest.application.resources.message_queries import (
    list_messages as list_session_messages,
)
from dtest.application.resources.messages import MessageService
from dtest.contracts.resources.api_schema import MessageResource, Page
from dtest.contracts.resources.message_schema import (
    MessageCreate,
    MessageCreateResult,
    MessageUpdate,
)

router = APIRouter(tags=["messages"])


def _idempotency_key(value: str | None) -> UUID | None:
    if value is None:
        return None
    if not value.strip():
        raise HTTPException(
            status_code=422, detail="Idempotency-Key cannot be blank."
        )
    # The existing database column is UUID-based.  Namespacing preserves
    # support
    # for opaque, standards-compliant header values without exposing that
    # detail.
    return uuid5(NAMESPACE_URL, f"message-create:{value}")


@router.post(
    "/messages",
    response_model=MessageCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_message_without_session(
    payload: MessageCreate,
    response: Response,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    *,
    user_id: CurrentUserId,
    db: DBSession,
):
    "Create a message, creating a session first when session_id is omitted."
    header_key = _idempotency_key(idempotency_key)
    if (
        payload.client_request_id
        and header_key
        and payload.client_request_id != header_key
    ):
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key does not match client_request_id.",
        )
    if header_key:
        payload.client_request_id = header_key

    result = await MessageService.create_from_request(db, user_id, payload)
    response.headers["Location"] = (
        f"/api/v1/messages/{result.message.message_id}"
    )
    response.headers["X-Session-Id"] = str(result.session_id)
    response.headers["X-Session-Created"] = str(result.session_created).lower()
    return result


@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_message(
    session_id: UUID,
    payload: MessageCreate,
    response: Response,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    *,
    user_id: CurrentUserId,
    db: DBSession,
):
    payload.session_id = session_id
    header_key = _idempotency_key(idempotency_key)
    if (
        payload.client_request_id
        and header_key
        and payload.client_request_id != header_key
    ):
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key does not match client_request_id.",
        )
    if header_key:
        payload.client_request_id = header_key
    result = await MessageService.create(db, user_id, payload)
    response.headers["Location"] = (
        f"/api/v1/messages/{result.message.message_id}"
    )
    return result


@router.get(
    "/sessions/{session_id}/messages", response_model=Page[MessageResource]
)
async def list_messages(
    session_id: UUID,
    params: ListQuery,
    user_id: CurrentUserId,
    db: DBSession,
):
    return await list_session_messages(db, user_id, session_id, params)


@router.get("/messages/{message_id}", response_model=MessageResource)
async def read_message(
    message_id: UUID,
    user_id: CurrentUserId,
    db: DBSession,
):
    return MessageResource.model_validate(
        await MessageService.read(db, user_id, message_id)
    )


@router.patch("/messages/{message_id}", response_model=MessageResource)
async def update_message(
    message_id: UUID,
    payload: MessageUpdate,
    user_id: CurrentUserId,
    db: DBSession,
):
    return MessageResource.model_validate(
        await MessageService.update(db, user_id, message_id, payload)
    )


@router.delete(
    "/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_message(
    message_id: UUID,
    user_id: CurrentUserId,
    db: DBSession,
):
    await MessageService.delete(db, user_id, message_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

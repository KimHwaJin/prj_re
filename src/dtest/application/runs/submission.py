"""One application boundary for unified Run start/resume requests."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from dtest.application.runs.service import PublicRunService
from dtest.contracts.errors import ApplicationError
from dtest.contracts.resources.run_schema import (
    PublicRunResource,
    RunResume,
    RunStart,
)
from dtest.contracts.run_request import RunRequest, TextContent


async def submit_request(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    payload: RunRequest,
    key: str | None,
) -> PublicRunResource:
    if not key or not key.strip():
        raise ApplicationError(400, "Idempotency-Key is required.")
    if len(key) > 255:
        raise ApplicationError(
            422, "Idempotency-Key must not exceed 255 characters."
        )
    if payload.command is not None:
        if payload.run_id is None or payload.resume_token is None:
            raise ApplicationError(
                422, "Resume requires run_id and resume_token"
            )
        return await PublicRunService.resume(
            db,
            user_id,
            session_id,
            payload.run_id,
            RunResume(
                command=payload.command.model_dump(
                    mode="json", exclude_unset=True
                ),
                resume_token=payload.resume_token,
            ),
            key,
        )
    if payload.input is None:
        raise ApplicationError(422, "A user input is required.")
    parts = payload.input.content
    if any(not isinstance(part, TextContent) for part in parts):
        raise ApplicationError(
            422,
            "Image/file input requires the future authorized "
            "attachment service; text input is supported now.",
        )
    text = "\n".join(
        part.text for part in parts if isinstance(part, TextContent)
    ).strip()
    if not text:
        raise ApplicationError(422, "A non-blank user message is required.")
    return await PublicRunService.create(
        db,
        user_id,
        session_id,
        RunStart(
            input={"messages": [{"role": "user", "content": text}]},
            main_model_name=payload.main_model_name,
        ),
        key,
    )

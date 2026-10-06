"""Stable event envelope; families and data can evolve independently."""

from typing import Any, Literal
from uuid import UUID
from pydantic import Field
from dtest.contracts.plan_interaction import StrictModel


class RunEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: dict[str, Any]


def public_event_payload(event, *, session_id, run_id):
    """Do not expose legacy checkpoint/log payloads or model JSON tokens."""
    payload = event.payload
    if payload.get("schema_version") == 1:
        return RunEvent.model_validate(
            {
                **payload,
                "sequence": event.sequence,
                "session_id": str(session_id),
                "run_id": str(run_id),
            }
        ).model_dump(mode="json")
    if event.event_type.startswith("task."):
        event_type = "run.updated"
        data = {
            k: payload[k] for k in ("status", "attempt_count") if k in payload
        }
    else:
        event_type = "activity.updated"
        data = {"kind": "legacy", "title": "작업 상태가 변경되었습니다."}
    return RunEvent(
        type=event_type,
        sequence=event.sequence,
        session_id=session_id,
        run_id=run_id,
        occurred_at=event.created_at.isoformat(),
        data=data,
    ).model_dump(mode="json")

"""Conversation input capability, independent of resource CRUD permissions."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field

from dtest.contracts.resources.run_schema import PublicRunStatus


class SessionActiveRun(BaseModel):
    run_id: UUID = Field(description="Stable public Run ID; use the existing Run GET/SSE for details.")
    status: PublicRunStatus = Field(description="Same lifecycle status as the public Run response.")


class SessionAvailability(BaseModel):
    status: Literal["available", "busy", "blocked"] = Field(
        description="Current conversation input capability; does not grant CRUD permissions.")
    allowed_actions: list[Literal["send_message", "respond_to_interaction"]] = Field(
        description="send_message starts a Run; respond_to_interaction resumes the current HITL. Empty locks input.")
    reason: Literal["processing", "waiting_external", "canceling", "recovery_required", "resource_unavailable"] | None = Field(
        description="Machine-readable explanation; null when input is available. Refresh Run for interaction details.")

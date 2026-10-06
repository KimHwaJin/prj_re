"""Immutable values captured by the command claim transaction."""

from dataclasses import dataclass
from uuid import UUID

from dtest.application.runs.claim_context import ExecutionClaim
from dtest.contracts.resources.run_schema import RunCreate
from dtest.application.runs.ownership import SessionExecution
from dtest.contracts.events import EventContext


@dataclass(frozen=True)
class ClaimedRun:
    claim: ExecutionClaim
    user_id: UUID
    session_id: UUID
    payload: RunCreate
    key: str
    namespace: str

    @property
    def command_id(self):
        return self.claim.run_id

    @property
    def owner(self):
        return SessionExecution(
            self.session_id, self.claim.lock_token, self.command_id, "api_run"
        )


@dataclass(frozen=True)
class ClaimedEvent:
    context: EventContext
    owner: SessionExecution

    @property
    def command_id(self):
        return self.context.command_id

    @property
    def session_id(self):
        return self.owner.session_id

    @property
    def namespace(self):
        return self.context.namespace


ClaimedCommand = ClaimedRun | ClaimedEvent

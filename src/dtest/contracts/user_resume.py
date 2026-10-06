"""Private service-to-Agent resume identity; never supplied by the frontend."""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict


from dtest.contracts.execution import InvocationNeedsRecovery


class UserResumeNeedsRecovery(InvocationNeedsRecovery):
    """A stopped user invocation needs reconciliation; other sessions may run."""


class UserResumeState(TypedDict, total=False):
    # Only one user command can be in flight in a session. Retain the latest
    # receipt, not an unbounded history of all answers.
    user_resume_receipt: dict[str, str]


def resume_identity(
    command_id: str, interrupt_id: str, command: Any
) -> dict[str, str]:
    encoded = json.dumps(
        command, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return {
        "command_id": command_id,
        "interrupt_id": interrupt_id,
        "digest": hashlib.sha256(encoded.encode()).hexdigest(),
    }


def resume_envelope(identity: dict[str, str], command: Any) -> dict[str, Any]:
    return {"__user_resume_v1__": identity, "value": command}

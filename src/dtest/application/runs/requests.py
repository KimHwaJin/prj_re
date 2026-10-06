"""Canonical request identity, idempotency replay and pinned model selection."""

import hashlib
import json

from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.application.runs.errors import InvalidRunRequest, RunConflict
from dtest.contracts.resources.run_schema import RunCreate


def select_model(name):
    from dtest.contracts.model_selection import current_catalog, ModelSelectionError
    try:
        return current_catalog().select(name).model_dump()
    except ModelSelectionError as exc:
        raise InvalidRunRequest(str(exc)) from None


def validate_model(reference):
    from dtest.contracts.model_selection import current_catalog, ModelSelectionError
    try:
        current_catalog().resolve(reference)
    except ModelSelectionError as exc:
        raise RunConflict(str(exc)) from None


def request_digest(payload: RunCreate) -> str:
    data = payload.model_dump(mode="json")
    if data["main_model_name"] is None:
        del data["main_model_name"]  # Preserve idempotency hashes for old clients.
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def validate_replay(previous: AgentRunModel, payload: RunCreate) -> None:
    digest = (previous.metadata_json or {}).get("_request_digest")
    if digest is None:
        # Pre-migration resumes used a different public contract. Do not
        # guess that an old key belongs to a newly tokenized command.
        metadata = {k: v for k, v in (previous.metadata_json or {}).items()
                    if k not in {"checkpoint_run_id", "requested_by_user_id", "_request_digest"}}
        original = RunCreate(input=previous.input_json, command=previous.command_json,
                             metadata=metadata, multitask_strategy=previous.multitask_strategy,
                             stream_mode=previous.stream_mode, stream_resumable=previous.stream_resumable,
                             on_disconnect=previous.on_disconnect)
        matches = previous.command_json is None and request_digest(original) == request_digest(payload)
    else:
        matches = digest == request_digest(payload)
    if not matches:
        raise RunConflict("Idempotency-Key was already used for a different request.")

"""Stable identity and checkpoint fields for a service-admitted initial turn."""

from __future__ import annotations

import hashlib
import json
from typing import TypedDict


class InitialRequestState(TypedDict, total=False):
    run_id: str
    initial_request_identity: dict[str, str] | None
    initial_request_receipt: dict[str, str] | None


def initial_identity(values: dict) -> dict[str, str]:
    # Exclude volatile request IDs, mutable project context and generated output.
    payload = {
        key: values.get(key)
        for key in (
            "user_id",
            "project_id",
            "session_id",
            "run_id",
            "user_request",
            "model_selection",
        )
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return {
        "command_id": str(values["run_id"]),
        "digest": hashlib.sha256(encoded.encode()).hexdigest(),
    }

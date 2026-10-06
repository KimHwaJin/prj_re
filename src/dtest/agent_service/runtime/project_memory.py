"""Current-quote-supported section patches; never share session observations."""

import json
import re
from langchain_core.messages import HumanMessage
from dtest.contracts.project_memory import (
    MemoryProposal,
    section_body,
    replace_section,
)

AUTO_SECTIONS = {"background", "analysis_preferences", "report_preferences"}
SESSION_ONLY = re.compile(
    r"이번(?:만|에는|은|에|\s*(?:분석|보고서|결과))|지금만|방금|이번에만|this\s+(?:time|report|analysis|result)|just\s+for\s+now",
    re.I,
)
PERSISTENT = re.compile(
    r"앞으로|항상|프로젝트|기억|기본(?:으로|값)|계속|매번|always|remember|from\s+now|project|default",
    re.I,
)
PREFERENCE = re.compile(
    r"보고서는|분석에서는|선호|목표|목적|보고서.*(?:작성|독자)|prefer|reports?\s+should|goal|purpose",
    re.I,
)
FORBIDDEN = re.compile(
    r"\d|https?://|(?:^|\s)/|[a-zA-Z]:[\\/]|\.(?:parquet|csv|xlsx|json)(?:$|\W)"
)


def validate_memory_proposals(reply, request):
    updates = getattr(reply, "memory_updates", [])
    if not updates:
        return
    context = request.runtime.context
    if (
        not getattr(context, "project_memory_auto_write", False)
        or getattr(context, "project_memory_policy", None) is None
    ):
        raise ValueError(
            "Automatic project memory writes are disabled; "
            "memory_updates must be "
            "empty"
        )
    if reply.kind == "planning":
        raise ValueError("Planning selection must not write memory")
    limits = context.project_memory_limits
    if len(updates) > limits.max_updates:
        raise ValueError("Too many project memory updates")
    if (
        "project_memory_reference" in request.state
        and request.state["project_memory_reference"] is None
    ):
        raise ValueError(
            "Memory input disabled or budget too small; updates must be empty"
        )
    snapshot = request.state.get("project_memory_snapshot")
    if (
        snapshot is None
        or snapshot["user_id"] != context.user_id
        or snapshot["project_id"] != context.project_id
    ):
        raise ValueError("Memory requires an owner-checked snapshot")
    payload = json.loads(
        next(
            m.content for m in request.messages if isinstance(m, HumanMessage)
        )
    )
    current = payload.get("request", "")
    if not isinstance(current, str):
        raise ValueError("No current request for memory source")
    reference = request.state.get("project_memory_reference")
    if reference is None:
        raise ValueError(
            "A bounded visible memory reference is required for "
            "automatic "
            "edits"
        )
    editable = reference["write_policy"]["editable_sections"]
    sections = set()
    for raw in updates:
        change = MemoryProposal.model_validate(
            raw.model_dump() if hasattr(raw, "model_dump") else raw
        )
        if change.section in sections:
            raise ValueError("Duplicate memory section")
        sections.add(change.section)
        if change.section not in editable:
            raise ValueError(
                "Cannot edit an omitted or ambiguous memory section"
            )
        if change.section not in AUTO_SECTIONS:
            raise ValueError(
                "Analysis findings require explicit project "
                "sharing, never automatic "
                "extraction"
            )
        if change.quote not in current or not change.quote.strip():
            raise ValueError(
                "Memory provenance must be an exact non-blank "
                "CURRENT user "
                "quote"
            )
        if (
            len(change.content) > limits.patch_max_chars
            or len(change.quote) > limits.patch_max_chars
        ):
            raise ValueError(
                "Memory replacement or provenance exceeds its configured limit"
            )
        if SESSION_ONLY.search(change.quote) or (
            SESSION_ONLY.search(current)
            and not PERSISTENT.search(change.quote)
        ):
            raise ValueError(
                "Session-only requests must not update project memory"
            )
        if not (
            PERSISTENT.search(change.quote)
            or PERSISTENT.search(current)
            or PREFERENCE.search(change.quote)
        ):
            raise ValueError(
                "No explicit durable project context or preference "
                "in the CURRENT "
                "quote"
            )
        if FORBIDDEN.search(change.content) or FORBIDDEN.search(change.quote):
            raise ValueError(
                "Automatic memory cannot share numeric statements, "
                "URLs or file paths; use explicit "
                "sharing"
            )
        if change.expected_version != snapshot["version"]:
            raise ValueError(
                "Memory proposal must echo the project document version"
            )
        before = section_body(snapshot["content"], change.section)
        if change.old_text != before:
            raise ValueError("Copy the complete current section body exactly")
        if " ".join(before.split()) == " ".join(change.content.split()):
            raise ValueError("Do not rewrite unchanged memory sections")
        replace_section(snapshot["content"], change)


def extract_memory_changes(schema, state):
    from dtest.agent_service.factory import json_output

    reply = json_output(schema)(state)
    return [update.model_dump() for update in reply.memory_updates]

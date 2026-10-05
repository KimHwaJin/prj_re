"""Catalog references are resolved from this invocation's Tool messages only."""

from copy import deepcopy
import json
from typing import Annotated
from langchain.tools import tool
from langgraph.prebuilt import InjectedState
from langchain_core.messages import HumanMessage, ToolMessage
from service_contracts.workflow_standard import normalize
from service_contracts.plan_review import new_review


def workflow_search_tool(retriever, catalog, *, context_max_chars=64000):
    @tool
    async def search_workflows(
        messages: Annotated[list, InjectedState("messages")],
    ) -> dict:
        """Retrieve E2E Workflow templates for the current request; metadata only."""
        payload = json.loads(
            next(m.content for m in messages if isinstance(m, HumanMessage))
        )
        result = (await retriever.search(payload["request"])).model_dump(mode="json")
        candidates = []
        invalid = 0
        used = 0
        omitted = 0
        for item in result["items"]:
            try:
                definition = normalize(
                    item["document"],
                    catalog.metadata,
                    workflow_id=item["workflow_id"],
                    definition_version=item["resource_revision"],
                )
            except (ValueError, KeyError, TypeError):
                invalid += 1
                continue
            candidate = {
                key: value for key, value in item.items() if key != "document"
            } | {"definition": definition}
            size = len(json.dumps(candidate, ensure_ascii=False))
            if used + size > context_max_chars:
                omitted += 1
                continue
            used += size
            candidates.append(candidate)
        # No template source code or arbitrary local runtime path is returned.
        return {
            "items": candidates,
            "diagnostics": result["diagnostics"],
            "invalid_templates": invalid,
            "context_omitted_templates": omitted,
        }

    return search_workflows


def retrieved_candidates(messages):
    found = {}
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "search_workflows":
            continue
        try:
            value = (
                json.loads(message.content)
                if isinstance(message.content, str)
                else message.content
            )
            for item in value.get("items", []):
                found[item["workflow_id"]] = item
        except (ValueError, TypeError, AttributeError, KeyError):
            raise ValueError("Invalid Workflow search result") from None
    return found


def resolve_recommendations(reply, messages, catalog, *, repair_limit, repair_attempts):
    found = retrieved_candidates(messages)
    chosen = []
    for proposal in reply.plans:
        if proposal.workflow_id is None:
            continue
        if proposal.workflow_id not in found:
            raise ValueError(
                "Recommended Workflow must be a candidate returned by this invocation"
            )
        if proposal.workflow_id in chosen:
            raise ValueError("Recommended Workflow must be distinct")
        chosen.append(proposal.workflow_id)
        item = found[proposal.workflow_id]
        # A recommendation selects a verified definition; it cannot silently
        # rewrite tools, decisions or its explicitly pinned execution policy.
        proposal.definition = deepcopy(item["definition"])
        proposal._catalog_reference = {
            key: item[key]
            for key in (
                "workflow_id",
                "content_sha256",
                "resource_revision",
                "search_revision",
                "similarity",
            )
        }
        definition = proposal.definition
        policy = {
            "allowed_modes": ["MULTI"]
            if definition.get("decisions")
            or any("when" in s for s in definition.get("steps", []))
            else ["SINGLE", "MULTI"],
            "repair_level_limit": repair_limit,
            "max_repair_attempts_limit": repair_attempts,
        }
        review = new_review(definition, proposal.input_values, catalog.metadata, policy)
        if review["document"]["execution"]["max_repair_attempts"] > repair_attempts:
            raise ValueError("Recommended Workflow exceeds the service repair limit")
    return reply

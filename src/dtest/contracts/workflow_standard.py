"""Public Workflow authoring → one existing execution plan. No IO or execution."""

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
from hashlib import sha256
import json

from jsonschema_rs import Draft202012Validator
from .workflow_validation import validate as validate_plan


class WorkflowStandardError(ValueError):
    pass


@lru_cache(maxsize=1)
def standard_schema():
    return json.loads(
        files("dtest.contracts")
        .joinpath("resources/workflow-standard.schema.json")
        .read_text()
    )


def normalize(document, catalog, *, workflow_id=None, definition_version=1):
    errors = [
        str(e)
        for e in Draft202012Validator(standard_schema()).iter_errors(document)
    ]
    if errors:
        raise WorkflowStandardError("; ".join(errors[:8]))
    original = deepcopy(document)
    body = original["workflow"]
    calls = []
    groups = set()
    by_id = {}
    for group in body["steps"]:
        if group["id"] in groups:
            raise WorkflowStandardError(
                "Duplicate Skill group ID: " + group["id"]
            )
        groups.add(group["id"])
        for call in group["tools"]:
            if call["id"] in by_id:
                raise WorkflowStandardError(
                    "Duplicate Tool call ID: " + call["id"]
                )
            calls.append((group["skill"], call))
            by_id[call["id"]] = call
    positions = {call["id"]: i for i, (_, call) in enumerate(calls)}
    decisions = body.get("decisions", [])
    decision_map = {d["id"]: d for d in decisions}
    if len(decisions) != len(decision_map):
        raise WorkflowStandardError("Duplicate decision ID")

    def convert(value, consumer=None, dependencies=None):
        if isinstance(value, list):
            return [convert(x, consumer, dependencies) for x in value]
        if not isinstance(value, dict):
            return value
        source = value.get("source")
        if source == "literal":
            return deepcopy(value)
        if source == "input":
            return {"source": "workflow_input", "name": value["name"]}
        if source == "agent_report":
            return {
                "source": "agent_report",
                "evidence_steps": deepcopy(value["evidence_calls"]),
            }
        if source == "agent_decision":
            decision = decision_map.get(value["decision_id"])
            if decision is None:
                raise WorkflowStandardError(
                    "Unknown decision: " + value["decision_id"]
                )
            if consumer is not None:
                for evidence in decision["after_calls"]:
                    if (
                        evidence not in positions
                        or positions[evidence] >= positions[consumer]
                    ):
                        raise WorkflowStandardError(
                            "Decision evidence must precede its consumer: "
                            + evidence
                        )
                    dependencies.add(evidence)
            return deepcopy(value)
        if source == "tool_output":
            producer = value["call_id"]
            if producer not in by_id:
                raise WorkflowStandardError("Unknown output call: " + producer)
            if (
                consumer is not None
                and positions[producer] >= positions[consumer]
            ):
                raise WorkflowStandardError(
                    "Output call must precede consumer: " + producer
                )
            tool = catalog["tools"].get(by_id[producer]["tool"], {})
            outputs = tool.get("outputs", {})
            if value["output"] not in outputs:
                raise WorkflowStandardError(
                    "Unknown registered output: "
                    + producer
                    + "."
                    + value["output"]
                )
            if dependencies is not None:
                dependencies.add(producer)
            return {
                "source": "step_output",
                "step_id": producer,
                "selector": deepcopy(outputs[value["output"]]["selector"]),
            }
        return {
            k: convert(v, consumer, dependencies) for k, v in value.items()
        }

    steps = []
    for skill, call in calls:
        deps = set()
        step = {
            "id": call["id"],
            "skill_id": skill,
            "tool_id": call["tool"],
            "description": call.get("description", call["tool"]),
            "arguments": convert(call.get("arguments", {}), call["id"], deps),
        }
        if "when" in call:
            step["when"] = convert(call["when"], call["id"], deps)
        if "parameter_controls" in call:
            step["parameter_controls"] = deepcopy(call["parameter_controls"])
        step["depends_on"] = sorted(deps, key=positions.__getitem__)
        steps.append(step)
    inputs = {}
    for name, item in body.get("inputs", {}).items():
        inputs[name] = {
            "title": name,
            "description": name,
            "kind": "parameter",
            "required": True,
            "editable": True,
            **deepcopy(item),
        }
    content_hash = sha256(
        json.dumps(
            original,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    result = {
        "schema_version": "2.0-draft",
        "workflow_id": workflow_id or "wf." + content_hash[:24],
        "definition_version": definition_version,
        "name": body["name"],
        "description": body.get("description", body["user_request"]),
        "goal": body["user_request"],
        "inputs": inputs,
        "steps": steps,
        "ordered_call_ids": [c["id"] for _, c in calls],
        "decisions": [
            {
                "id": d["id"],
                "after_steps": deepcopy(d["after_calls"]),
                "instruction": d["instruction"],
                "output_schema": deepcopy(d["output_schema"]),
            }
            for d in decisions
        ],
        "expected_outputs": convert(body["expected_outputs"]),
    }
    if "execution" in body:
        result["execution"] = deepcopy(body["execution"])
    errors = validate_plan(result, catalog)
    if errors:
        raise WorkflowStandardError("; ".join(errors[:8]))
    return result


def classification(plan):
    return (
        "adaptive"
        if plan["decisions"] or any("when" in c for c in plan["steps"])
        else "static"
    )

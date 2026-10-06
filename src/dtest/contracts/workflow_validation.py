"""Pure Workflow structure and asset-reference validation; never runs Tools."""

import json
import keyword
from functools import lru_cache
from importlib.resources import files

from jsonschema_rs import Draft202012Validator

from .tool_bindings import binding_errors


@lru_cache(maxsize=1)
def workflow_schema():
    return json.loads(
        files("dtest.contracts")
        .joinpath("resources/workflow-definition.schema.json")
        .read_text()
    )


def bindings(value):
    if isinstance(value, dict):
        if isinstance(value.get("source"), str) and value["source"] in {
            "workflow_input",
            "step_output",
            "agent_decision",
            "system_context",
            "literal",
        }:
            yield value
            return  # Literal JSON is data; never traverse it as executable bindings.
        for child in value.values():
            yield from bindings(child)
    elif isinstance(value, list):
        for child in value:
            yield from bindings(child)


def remote_references(value):
    if isinstance(value, dict):
        if "$ref" in value or "$dynamicRef" in value:
            return True
        return any(remote_references(child) for child in value.values())
    if isinstance(value, list):
        return any(remote_references(child) for child in value)
    return False


def validate(document, catalog):
    schema = workflow_schema()
    errors = [
        str(error)
        for error in Draft202012Validator(schema).iter_errors(document)
    ]
    if errors:
        return errors

    steps = {item["id"]: item for item in document["steps"]}
    order = document.get("ordered_call_ids")
    if order is not None and (
        len(order) != len(steps)
        or set(order) != set(steps)
        or order != [s["id"] for s in document["steps"]]
    ):
        errors.append(
            "ordered_call_ids must list every call once in array order"
        )
    decisions = {item["id"]: item for item in document["decisions"]}
    outputs = {item["id"]: item for item in document["expected_outputs"]}
    for name, items, index in [
        ("step", document["steps"], steps),
        ("decision", document["decisions"], decisions),
        ("output", document["expected_outputs"], outputs),
    ]:
        if len(items) != len(index):
            errors.append(f"duplicate {name} id")
    if len(steps) + len(decisions) + len(outputs) != len(
        set(steps) | set(decisions) | set(outputs)
    ):
        errors.append("step, decision and output ids must be distinct")

    parents = {id: set(item["depends_on"]) for id, item in steps.items()}
    for id, deps in parents.items():
        if deps - steps.keys():
            errors.append(
                f"{id}: unknown dependency {sorted(deps - steps.keys())}"
            )

    def ancestors(id):
        result = set()
        pending = list(parents.get(id, set()))
        while pending:
            parent = pending.pop()
            if parent in result:
                continue
            result.add(parent)
            pending.extend(parents.get(parent, set()))
        if id in result:
            errors.append(f"dependency cycle at {id}")
        return result

    upstream = {id: ancestors(id) for id in steps}

    for id, item in document["inputs"].items():
        check_value_schema(item["value_schema"], f"input {id}", errors)
        if "default" in item and not remote_references(item["value_schema"]):
            try:
                if not Draft202012Validator(item["value_schema"]).is_valid(
                    item["default"]
                ):
                    errors.append(
                        f"input {id}: default does not satisfy value_schema"
                    )
            except (ValueError, TypeError):
                pass

    for id, item in decisions.items():
        check_value_schema(item["output_schema"], f"decision {id}", errors)
        for evidence in item["after_steps"]:
            if evidence not in steps:
                errors.append(
                    f"decision {id}: unknown evidence step {evidence}"
                )
            elif "when" in steps[evidence]:
                errors.append(
                    f"decision {id}: conditional evidence requires an explicit branch contract; not supported in this draft"
                )

    def check_binding(binding, label, consumer=None):
        source = binding["source"]
        if (
            source == "workflow_input"
            and binding["name"] not in document["inputs"]
        ):
            errors.append(f"{label}: unknown input {binding['name']}")
        elif source == "step_output":
            producer = binding["step_id"]
            if producer not in steps:
                errors.append(f"{label}: unknown output step {producer}")
            elif consumer is not None:
                if consumer in steps and producer not in upstream[consumer]:
                    errors.append(
                        f"{label}: output {producer} is not an upstream dependency"
                    )
                producer_guard = steps[producer].get("when")
                consumer_guard = consumer_guard_for(consumer, steps, outputs)
                if (
                    producer_guard is not None
                    and producer_guard != consumer_guard
                ):
                    errors.append(
                        f"{label}: conditional output {producer} needs the same explicit guard"
                    )
        elif source == "agent_decision":
            id = binding["decision_id"]
            if id not in decisions:
                errors.append(f"{label}: unknown decision {id}")
            elif consumer in steps:
                needed = set(decisions[id]["after_steps"])
                if not needed <= upstream[consumer]:
                    errors.append(
                        f"{label}: decision {id} is consumed before evidence steps complete"
                    )

    for id, item in steps.items():
        skill = catalog["skills"].get(item["skill_id"])
        tool = catalog["tools"].get(item["tool_id"])
        if skill is None:
            errors.append(f"{id}: unregistered skill {item['skill_id']}")
        elif item["tool_id"] not in skill["tools"]:
            errors.append(
                f"{id}: tool is not part of skill {item['skill_id']}"
            )
        if tool is None:
            errors.append(f"{id}: unregistered tool {item['tool_id']}")
        else:
            provided = set(item["arguments"])
            if any(
                not name.isidentifier() or keyword.iskeyword(name)
                for name in provided
            ):
                errors.append(
                    f"{id}: Tool argument names must be Python identifiers, not Workflow IDs"
                )
            if not tool.get("allows_extra_arguments", False):
                unknown = provided - set(tool["parameters"])
                if unknown:
                    errors.append(
                        f"{id}: unknown tool arguments {sorted(unknown)}"
                    )
            missing = set(tool["required_parameters"]) - provided
            if missing:
                errors.append(
                    f"{id}: missing tool arguments {sorted(missing)}"
                )
        if tool is not None:
            errors.extend(binding_errors(item, document, tool))
            for name, binding in item["arguments"].items():
                if binding["source"] == "literal" and name in tool.get(
                    "parameter_controls", {}
                ):
                    if not Draft202012Validator(
                        tool["parameter_controls"][name]["value_schema"]
                    ).is_valid(binding["value"]):
                        errors.append(
                            f"{id}: literal {name} violates its Tool parameter schema"
                        )
        for name, control in item.get("parameter_controls", {}).items():
            check_value_schema(
                control["value_schema"], f"{id}.{name} control", errors
            )
            binding = item["arguments"].get(name)
            if (
                tool is not None
                and "parameter_controls" in tool
                and control["editable"]
            ):
                allowed = tool["parameter_controls"].get(name)
                if allowed is None or not allowed["editable"]:
                    errors.append(
                        f"{id}: Tool parameter is not user editable: {name}"
                    )
            if binding is None:
                errors.append(
                    f"{id}: parameter control requires an explicit argument binding: {name}"
                )
            elif control["editable"] and binding["source"] not in {
                "literal",
                "agent_decision",
            }:
                errors.append(
                    f"{id}.parameter_controls.{name}: object/input/system references cannot be edited as Tool parameter values; "
                    f"actual source={binding['source']}. Remove this Step control. "
                    + (
                        f"Edit inputs.{binding['name']}.editable instead."
                        if binding["source"] == "workflow_input"
                        else "Keep runtime references read only."
                    )
                )
            elif binding["source"] == "literal" and not remote_references(
                control["value_schema"]
            ):
                try:
                    if not Draft202012Validator(
                        control["value_schema"]
                    ).is_valid(binding["value"]):
                        errors.append(
                            f"{id}: literal {name} violates its parameter control schema"
                        )
                except (ValueError, TypeError):
                    pass
        for binding in bindings([item["arguments"], item.get("when")]):
            check_binding(binding, id, id)

    for id, item in outputs.items():
        source = item["source"]
        if source["source"] == "agent_report":
            if item["kind"] != "report" or item["format"] not in {
                "markdown",
                "html",
            }:
                errors.append(
                    f"output {id}: agent_report must be a report in markdown/html"
                )
            unknown = set(source["evidence_steps"]) - steps.keys()
            if unknown:
                errors.append(
                    f"output {id}: unknown report evidence {sorted(unknown)}"
                )
        else:
            check_binding(source, f"output {id}", id)
        for binding in bindings(item.get("when")):
            check_binding(binding, f"output {id}")

    policy = document.get("execution", {})
    mode = policy.get("mode")
    if mode == "SINGLE" and (
        decisions or any("when" in item for item in steps.values())
    ):
        errors.append(
            "SINGLE cannot contain post-result decisions or "
            "conditional steps in this "
            "draft"
        )
    if policy.get("review_mode") == "every_n_tools":
        if "review_interval_tools" not in policy:
            errors.append("every_n_tools requires review_interval_tools")
    elif "review_interval_tools" in policy:
        errors.append("review_interval_tools is only valid with every_n_tools")
    if mode == "SINGLE" and policy.get("review_mode") in {
        "every_tool",
        "every_n_tools",
    }:
        errors.append(
            "SINGLE cannot interleave Agent review with Tool execution"
        )

    return sorted(set(errors))


def consumer_guard_for(id, steps, outputs):
    return (steps.get(id) or outputs.get(id) or {}).get("when")


def check_value_schema(schema, label, errors):
    # Embedded value schemas have no remote loading or code execution mechanism.
    if remote_references(schema):
        errors.append(f"{label}: embedded schema references are not supported")
        return
    try:
        Draft202012Validator(schema)
    except (ValueError, TypeError) as error:
        errors.append(f"{label}: invalid embedded JSON Schema: {error}")

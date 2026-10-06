"""Pure Tool parameter policy shared by planning, API review and execution.

The deployed catalogue opts parameters into user forms. Workflow controls can
narrow that policy. Runtime object/path bindings never become literal editors.
"""

from copy import deepcopy

from pydantic import Field

from dtest.contracts.plan_interaction import StrictModel


class ToolParameterControl(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    editable: bool = Field(strict=True)
    value_schema: dict | bool


def intersect_schemas(*schemas):
    """Keep constraints conjunctive without growing duplicate allOf wrappers."""
    parts = []

    def add(schema):
        if schema is None or schema is True:
            return
        if isinstance(schema, dict) and set(schema) == {"allOf"}:
            for part in schema["allOf"]:
                add(part)
        elif schema not in parts:
            parts.append(deepcopy(schema))

    for schema in schemas:
        add(schema)
    if False in parts:
        return False
    return parts[0] if len(parts) == 1 else {"allOf": parts} if parts else True


def value_schema(tool, step, name, decisions=None):
    binding = step["arguments"][name]
    registered = tool.get("parameter_controls", {}).get(name, {})
    control = step.get("parameter_controls", {}).get(name, {})
    decision = (decisions or {}).get(binding.get("decision_id"), {})
    return intersect_schemas(
        registered.get("value_schema"),
        control.get("value_schema"),
        decision.get("output_schema"),
    )


def editable_schema(tool, step, name, decisions):
    binding = step["arguments"][name]
    if binding["source"] not in {"literal", "agent_decision"}:
        return None
    registered = tool.get("parameter_controls", {}).get(name)
    control = step.get("parameter_controls", {}).get(name)
    # Presence of a catalogue policy opts the whole Tool into explicit allowlisting.
    if "parameter_controls" in tool and (
        registered is None or not registered["editable"]
    ):
        return None
    if control is not None and not control["editable"]:
        return None
    if (
        registered is None
        and control is None
        and binding["source"] != "agent_decision"
    ):
        return None
    return value_schema(tool, step, name, decisions)


def materialize_defaults(document, catalog):
    """Freeze only declared JSON defaults, keeping supplied bindings untouched."""
    result = deepcopy(document)
    origins = {}
    decisions = {d["id"]: d for d in result["decisions"]}
    for step in result["steps"]:
        tool = catalog["tools"][step["tool_id"]]
        origins[step["id"]] = {
            name: "agent" if binding["source"] == "literal" else "unresolved"
            for name, binding in step["arguments"].items()
        }
        for name, control in tool.get("parameter_controls", {}).items():
            literal_allowed = "literal" in tool.get(
                "parameter_bindings", {}
            ).get(name, {}).get("allowed_sources", ["literal"])
            if (
                name not in step["arguments"]
                and control.get("has_default")
                and literal_allowed
            ):
                step["arguments"][name] = {
                    "source": "literal",
                    "value": deepcopy(control["default"]),
                }
                origins[step["id"]][name] = "tool_default"
            binding = step["arguments"].get(name)
            if binding is None or binding["source"] not in {
                "literal",
                "agent_decision",
            }:
                continue
            declared = step.setdefault("parameter_controls", {}).get(name)
            step["parameter_controls"][name] = {
                "editable": control["editable"]
                and (declared is None or declared["editable"]),
                "value_schema": intersect_schemas(
                    control["value_schema"],
                    declared.get("value_schema") if declared else None,
                ),
            }
            if binding["source"] == "agent_decision":
                decision = decisions[binding["decision_id"]]
                decision["output_schema"] = value_schema(
                    tool, step, name, decisions
                )
    return result, origins

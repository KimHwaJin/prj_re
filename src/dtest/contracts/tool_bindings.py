"""Registered parameter binding policy, independent of Tool names and implementations."""

from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator
from .plan_interaction import StrictModel

BindingSource = Literal[
    "literal",
    "workflow_input",
    "step_output",
    "agent_decision",
    "system_context",
]


class ParameterBindingPolicy(StrictModel):
    allowed_sources: list[BindingSource] = Field(min_length=1, max_length=5)
    input_kind: Literal["data_reference", "parameter"] | None = None
    required: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.allowed_sources)) != len(self.allowed_sources):
            raise ValueError("Parameter binding sources must be unique")
        if self.input_kind is not None and self.allowed_sources != [
            "workflow_input"
        ]:
            raise ValueError(
                "input_kind requires only workflow_input as its binding source"
            )
        return self


def parameter_bindings(declared, parameters, controls=None):
    if not isinstance(declared, dict):
        raise ValueError("Tool parameter_bindings must be a mapping")
    result = {}
    for name, raw in declared.items():
        if name not in parameters:
            raise ValueError(f"Unknown binding policy Tool parameter: {name}")
        policy = ParameterBindingPolicy.model_validate(raw).model_dump(
            exclude_none=True
        )
        if (controls or {}).get(name, {}).get(
            "editable"
        ) and "literal" not in policy["allowed_sources"]:
            raise ValueError(
                f"Editable Tool parameter must allow literal bindings: {name}"
            )
        result[name] = policy
    return result


def binding_errors(step, document, tool):
    errors = []
    for name, policy in tool.get("parameter_bindings", {}).items():
        binding = step["arguments"].get(name)
        location = f"{step['id']}.arguments.{name}"
        if binding is None:
            if policy.get("required"):
                errors.append(
                    f"{location}: registered policy requires an explicit binding"
                )
            continue
        if binding["source"] not in policy["allowed_sources"]:
            errors.append(
                f"{location}: binding source {binding['source']} is not allowed; use {policy['allowed_sources']}"
            )
            continue
        kind = policy.get("input_kind")
        if kind is not None:
            field = document["inputs"].get(binding.get("name"), {})
            if field.get("kind") != kind:
                errors.append(
                    f"{location}: referenced Workflow input must have kind={kind}"
                )
    return errors


def inherit_parameter_policy(info, origin):
    """Signature-preserving local aliases retain the registered authorization policy."""
    result = deepcopy(info)
    for key in ("parameter_bindings", "parameter_controls"):
        if key in origin:
            result[key] = deepcopy(origin[key])
    return result

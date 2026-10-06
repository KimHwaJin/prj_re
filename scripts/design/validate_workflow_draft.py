"""Offline validation of a design draft; does not run Tools or submit to Executor.

Uses jsonschema_rs already present in the development environment. It is not an
installed application command and is not called by the current Workflow API.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import json
from pathlib import Path

from jsonschema_rs import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/design/agentic-workflow-contract"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


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


def validate(document, catalog, *, repository_root: Path | None = None):
    schema = read(CONTRACT / "workflow-definition.schema.json")
    errors = [
        str(error)
        for error in Draft202012Validator(schema).iter_errors(document)
    ]
    if errors:
        return errors

    steps = {item["id"]: item for item in document["steps"]}
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
        for name, control in item.get("parameter_controls", {}).items():
            check_value_schema(
                control["value_schema"], f"{id}.{name} control", errors
            )
            binding = item["arguments"].get(name)
            if binding is None:
                errors.append(
                    f"{id}: parameter control requires an explicit argument binding: {name}"
                )
            elif control["editable"] and binding["source"] not in {
                "literal",
                "agent_decision",
            }:
                errors.append(
                    f"{id}: object/input/system references cannot be edited as Tool parameter values"
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

    if repository_root is not None:
        verify_repository_catalog(catalog, repository_root, errors)
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


def verify_repository_catalog(catalog, repository_root, errors):
    if catalog.get("catalog_scope") != "repository_subset":
        errors.append(
            "illustration catalog cannot be verified as repository assets"
        )
        return
    import yaml

    asset_root = (
        repository_root / "src/dtest/agent_service/agents/analysis/workflow"
    )
    registry = yaml.safe_load(
        (asset_root / "tools/tool_registry.yaml").read_text()
    )["tools"]
    skills = yaml.safe_load(
        (asset_root / "skills/skill_index.yaml").read_text()
    )["skills"]
    for id, item in catalog["tools"].items():
        actual = registry.get(id)
        if actual is None:
            errors.append(f"catalog: {id} missing from repository registry")
            continue
        expected_file = str(
            (asset_root / "tools" / actual["source"]).relative_to(
                repository_root
            )
        )
        if (
            item.get("source_file") != expected_file
            or item.get("function_name") != actual["function_name"]
        ):
            errors.append(f"catalog: {id} source does not match registry")
            continue
        module = ast.parse((repository_root / expected_file).read_text())
        function = next(
            (
                node
                for node in module.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == item["function_name"]
            ),
            None,
        )
        if function is None:
            errors.append(f"catalog: {id} function not found")
            continue
        positional = function.args.posonlyargs + function.args.args
        parameters = [
            argument.arg for argument in positional + function.args.kwonlyargs
        ]
        count = len(positional) - len(function.args.defaults)
        required = [arg.arg for arg in positional[:count]] + [
            arg.arg
            for arg, default in zip(
                function.args.kwonlyargs, function.args.kw_defaults
            )
            if default is None
        ]
        if (
            parameters != item["parameters"]
            or required != item["required_parameters"]
            or (function.args.kwarg is not None)
            != item["allows_extra_arguments"]
        ):
            errors.append(f"catalog: {id} signature does not match source")
    for id, item in catalog["skills"].items():
        if id not in skills or item["tools"] != [
            tool["tool"] for tool in skills[id]["tools"]
        ]:
            errors.append(
                f"catalog: {id} skill membership does not match repository"
            )


def self_check(repository_root):
    repo = read(CONTRACT / "examples/quality-review.repository.json")
    catalog = read(CONTRACT / "examples/repository-catalog.json")
    full = read(CONTRACT / "examples/full-analysis.fixture.json")
    fixture = read(CONTRACT / "examples/illustration-catalog.json")
    results = []

    def check(name, document, assets, expected_valid, repo_root=None):
        errors = validate(document, assets, repository_root=repo_root)
        results.append(
            {
                "name": name,
                "expected_valid": expected_valid,
                "passed": (not errors) == expected_valid,
                "errors": errors,
            }
        )

    check("repository_example", repo, catalog, True, repository_root)
    check("full_analysis_illustration", full, fixture, True)
    check("illustration_rejected_by_real_catalog", full, catalog, False)

    mutations = {
        "source_code_in_workflow": lambda value: value["steps"][0].update(
            {"code": "print('unregistered')"}
        ),
        "ambiguous_parameter_binding": lambda value: value["steps"][0][
            "arguments"
        ]["parquet_path"].update({"value": "/tmp/arbitrary"}),
        "dependency_cycle": lambda value: value["steps"][0][
            "depends_on"
        ].append("outliers"),
        "unknown_input": lambda value: value["steps"][0]["arguments"][
            "parquet_path"
        ].update({"name": "missing_input"}),
        "unknown_tool_argument": lambda value: value["steps"][0][
            "arguments"
        ].update({"unexpected": {"source": "literal", "value": 1}}),
        "unknown_decision": lambda value: value["steps"][3]["arguments"][
            "method"
        ].update({"decision_id": "unknown"}),
        "decision_before_evidence": lambda value: value["steps"][3].update(
            {"depends_on": ["load"]}
        ),
        "unguarded_conditional_output": lambda value: value[
            "expected_outputs"
        ][2].pop("when"),
        "post_result_decision_in_single": lambda value: value[
            "execution"
        ].update({"mode": "SINGLE"}),
        "duplicate_step_id": lambda value: value["steps"].append(
            deepcopy(value["steps"][0])
        ),
        "invalid_input_default": lambda value: value["inputs"][
            "dataset"
        ].update({"default": 0}),
        "missing_review_interval": lambda value: value["execution"].update(
            {"review_mode": "every_n_tools"}
        ),
        "unknown_operator": lambda value: value["steps"][3]["when"].update(
            {"op": "python_eval"}
        ),
        "invalid_decision_schema": lambda value: value["decisions"][0].update(
            {"output_schema": {"type": "python"}}
        ),
        "external_schema_reference": lambda value: value["inputs"][
            "dataset"
        ].update(
            {
                "value_schema": {
                    "$ref": "https://example.invalid/value-schema"
                },
                "default": "file-001",
            }
        ),
        "editable_object_reference": lambda value: value["steps"][1].update(
            {
                "parameter_controls": {
                    "data": {
                        "editable": True,
                        "value_schema": {"type": "string"},
                    }
                }
            }
        ),
        "control_without_argument": lambda value: value["steps"][1].update(
            {
                "parameter_controls": {
                    "missing": {
                        "editable": True,
                        "value_schema": {"type": "string"},
                    }
                }
            }
        ),
    }
    for name, mutate in mutations.items():
        document = deepcopy(repo)
        mutate(document)
        check(name, document, catalog, False)
    missing = deepcopy(full)
    missing["steps"][0]["arguments"].pop("query")
    check("missing_required_tool_argument", missing, fixture, False)
    fixed = deepcopy(repo)
    fixed["steps"] = fixed["steps"][:3]
    fixed["decisions"] = []
    fixed["execution"]["mode"] = "SINGLE"
    fixed["expected_outputs"] = fixed["expected_outputs"][:2]
    check("fixed_steps_single", fixed, catalog, True)
    return {
        "schema_version": "2.0-draft",
        "scope": (
            "offline schema and semantic validation only; no LLM, Tool, "
            "API, DB or Executor "
            "execution"
        ),
        "passed": all(item["passed"] for item in results),
        "checks": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, nargs="?")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.self_check:
        result = self_check(arguments.repository_root)
    else:
        if arguments.document is None or arguments.catalog is None:
            parser.error(
                "document and --catalog are required unless "
                "--self-check is "
                "used"
            )
        catalog = read(arguments.catalog)
        errors = validate(
            read(arguments.document),
            catalog,
            repository_root=arguments.repository_root,
        )
        result = {
            "passed": not errors,
            "catalog_scope": catalog.get("catalog_scope"),
            "errors": errors,
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "check_count": len(result.get("checks", [])),
                "errors": result.get("errors", []),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

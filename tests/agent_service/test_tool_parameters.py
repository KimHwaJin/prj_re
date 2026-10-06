"""Real catalogue → code-free form → schema edits → frozen Executor arguments."""

import ast
from copy import deepcopy
from importlib.resources import files
import json

import pytest

from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from dtest.agent_service.agents.analysis.planning.parameters import (
    parameter_controls,
)
from dtest.agent_service.agents.analysis.execution.compiler import (
    compile_steps,
    validate_decisions,
)
from dtest.agent_service.agents.analysis.workflow.tools.generate_tool_registry import (
    build_registry,
)
from dtest.contracts.plan_review import (
    new_review,
    patch_review,
    freeze_approval,
    PlanReviewError,
)
from dtest.contracts.plan_projection import plan_view

POLICY = {
    "allowed_modes": ["MULTI"],
    "repair_level_limit": 4,
    "max_repair_attempts_limit": 3,
}
CONTEXT = {
    "user_id": "u",
    "project_id": "p",
    "session_id": "s",
    "public_run_id": "r",
}
DATASETS = {
    "default-nce": {
        "scope": "GLOBAL",
        "runtime_path": "/workspace/pv/default_data/nce.parquet",
    }
}


def document():
    return json.loads(
        files("dtest.agent_service.agents.analysis.planning")
        .joinpath("fixtures/quality-review.json")
        .read_text()
    )


def review(doc=None):
    catalog = AssetCatalog()
    return new_review(
        doc or document(), {"dataset": "default-nce"}, catalog.metadata, POLICY
    ), catalog


def parameter(state, step, name):
    return next(
        p
        for s in plan_view(state)["steps"]
        if s["step_id"] == step
        for p in s["parameters"]
        if p["name"] == name
    )


def edit(state, changes, **kw):
    action = {
        "action": "edit_plan",
        "plan_id": state["plan_id"],
        "plan_revision": state["plan_revision"],
        "step_changes": changes,
        **kw,
    }
    return patch_review(state, action, datasets=DATASETS, context=CONTEXT)


def test_omitted_optional_columns_appear_with_actual_null_default_but_method_stays_deferred():
    doc = document()
    state, catalog = review(doc)
    assert doc == document(), "normalization must not mutate a shared Workflow"
    for step in ("statistics", "outliers"):
        field = parameter(state, step, "columns")
        assert (
            field["editable"] and field["has_value"] and field["value"] is None
        )
        assert (
            field["origin"] == "tool_default"
            and field["title"] == "분석할 컬럼"
        )
    method = parameter(state, "outliers", "method")
    assert (
        method["kind"] == "deferred"
        and not method["has_value"]
        and method["origin"] == "unresolved"
    )
    assert "value" not in method
    for step, name in [
        ("load", "parquet_path"),
        ("profile", "data"),
        ("statistics", "data"),
    ]:
        assert not parameter(state, step, name)["editable"]
    assert (
        "parquet_path"
        not in catalog.metadata["tools"]["data_load"]["parameter_controls"]
    )
    assert "data" not in state["document"]["steps"][0]["arguments"]
    assert "code" not in json.dumps(
        plan_view(state)
    ) and "/workspace/" not in json.dumps(plan_view(state))


def test_literal_edit_preserves_source_and_freezes_exact_columns_for_executor():
    state, catalog = review()
    changed = edit(
        state,
        [
            {
                "step_id": "statistics",
                "parameter": "columns",
                "value": ["max_val"],
            }
        ],
    )
    assert (
        state["document"]["steps"][2]["arguments"]["columns"]["value"] is None
    )
    assert changed["plan_revision"] == 2
    field = parameter(changed, "statistics", "columns")
    assert field["value"] == ["max_val"] and field["origin"] == "user"
    approved = edit(changed, [], action="approve_plan")
    frozen = freeze_approval(
        approved,
        catalog.sources,
        catalog.skill_sources,
        CONTEXT,
        catalog.revision,
        DATASETS,
    )
    result = compile_steps(frozen, [frozen["steps"][2]], {}, 0)
    assert "columns=['max_val']" in result[0]["payload"]["source"]["content"]
    assert (
        frozen["tool_sources"]["compute_statistics"]["code"]
        == catalog.sources["compute_statistics"]["code"]
    )
    cleared = edit(
        changed,
        [{"step_id": "statistics", "parameter": "columns", "value": None}],
    )
    assert parameter(cleared, "statistics", "columns")["value"] is None


@pytest.mark.parametrize(
    "value", ["max_val", [], [""], [123], ["x", "x"], {"x": 1}]
)
def test_invalid_column_edits_leave_review_and_revision_unchanged(value):
    state, _ = review()
    before = deepcopy(state)
    with pytest.raises(PlanReviewError, match="schema"):
        edit(
            state,
            [
                {
                    "step_id": "statistics",
                    "parameter": "columns",
                    "value": value,
                }
            ],
        )
    assert state == before


def test_workflow_can_narrow_schema_or_lock_but_cannot_widen_catalogue():
    doc = document()
    doc["steps"][2]["arguments"]["columns"] = {
        "source": "literal",
        "value": ["max_val"],
    }
    doc["steps"][2]["parameter_controls"] = {
        "columns": {
            "editable": True,
            "value_schema": {
                "type": "array",
                "items": {"enum": ["max_val"]},
                "minItems": 1,
            },
        }
    }
    state, _ = review(doc)
    with pytest.raises(PlanReviewError, match="schema"):
        edit(
            state,
            [
                {
                    "step_id": "statistics",
                    "parameter": "columns",
                    "value": ["other"],
                }
            ],
        )
    doc["steps"][2]["parameter_controls"]["columns"]["editable"] = False
    state, _ = review(doc)
    assert not parameter(state, "statistics", "columns")["editable"]
    with pytest.raises(PlanReviewError, match="read only"):
        edit(
            state,
            [
                {
                    "step_id": "statistics",
                    "parameter": "columns",
                    "value": ["max_val"],
                }
            ],
        )
    doc["steps"][2]["parameter_controls"]["data"] = {
        "editable": True,
        "value_schema": True,
    }
    with pytest.raises(
        PlanReviewError, match="not user editable|references cannot"
    ):
        review(doc)


def test_agent_literals_and_workflow_inputs_cannot_bypass_registered_schema():
    doc = document()
    doc["steps"][2]["arguments"]["columns"] = {
        "source": "literal",
        "value": "wrong",
    }
    with pytest.raises(PlanReviewError, match="Tool parameter schema"):
        review(doc)
    doc["inputs"]["columns"] = {
        "title": "컬럼",
        "description": "사용자 지정",
        "kind": "parameter",
        "required": True,
        "editable": True,
        "value_schema": True,
    }
    doc["steps"][2]["arguments"]["columns"] = {
        "source": "workflow_input",
        "name": "columns",
    }
    state, _ = review(doc)
    assert not parameter(state, "statistics", "columns")["editable"]
    with pytest.raises(PlanReviewError, match="parameter schema"):
        edit(state, [], input_values={"columns": "wrong"})
    assert edit(state, [], input_values={"columns": ["max_val"]})[
        "input_values"
    ]["columns"] == ["max_val"]
    with pytest.raises(PlanReviewError, match="Required input"):
        edit(state, [], action="approve_plan")


def test_result_decision_is_constrained_even_if_model_declares_broader_schema():
    doc = document()
    doc["decisions"][1]["output_schema"] = {"type": "string"}
    state, catalog = review(doc)
    approved = edit(state, [], action="approve_plan")
    frozen = freeze_approval(
        approved,
        catalog.sources,
        catalog.skill_sources,
        CONTEXT,
        catalog.revision,
        DATASETS,
    )
    with pytest.raises(PlanReviewError, match="schema"):
        validate_decisions(
            frozen,
            {"outlier_method": "arbitrary"},
            ["load", "profile", "statistics"],
        )
    assert (
        validate_decisions(
            frozen,
            {"outlier_method": "zscore"},
            ["load", "profile", "statistics"],
        )["outlier_method"]
        == "zscore"
    )


def test_supplied_agent_value_and_binding_are_not_overwritten_by_defaults():
    doc = document()
    doc["steps"][2]["arguments"]["columns"] = {
        "source": "literal",
        "value": ["known"],
    }
    doc["steps"][3]["arguments"]["columns"] = {
        "source": "agent_decision",
        "decision_id": "selected_columns",
    }
    doc["decisions"].append(
        {
            "id": "selected_columns",
            "after_steps": ["statistics"],
            "instruction": "실제 결과로 선택",
            "output_schema": {"type": "array", "items": {"type": "string"}},
        }
    )
    state, _ = review(doc)
    assert parameter(state, "statistics", "columns")["origin"] == "agent"
    assert parameter(state, "statistics", "columns")["value"] == ["known"]
    assert parameter(state, "outliers", "columns")["kind"] == "deferred"


def control(schema):
    return {
        "title": "옵션",
        "description": "실제 기본값",
        "editable": True,
        "value_schema": schema,
    }


def test_ast_defaults_include_keyword_only_false_zero_null_and_tuple_without_imports():
    function = ast.parse(
        "def demo(count=0, flag=False, names=('a', 'b'), *, "
        "note=None):\n    raise RuntimeError('never "
        "run')"
    ).body[0]
    fields = parameter_controls(
        function,
        {
            "count": control({"type": "integer"}),
            "flag": control({"type": "boolean"}),
            "names": control({"type": "array"}),
            "note": control({"type": "null"}),
        },
    )
    assert [
        fields[n]["default"] for n in ("count", "flag", "names", "note")
    ] == [0, False, ["a", "b"], None]
    assert all(f["has_default"] for f in fields.values())


@pytest.mark.parametrize(
    "expression", ["side_effect()", "GLOBAL_VALUE", 'float("nan")']
)
def test_nonliteral_defaults_cannot_be_exposed_or_executed(expression):
    function = ast.parse(f"def demo(value={expression}):\n    pass").body[0]
    with pytest.raises(ValueError, match="literal JSON"):
        parameter_controls(function, {"value": control(True)})


def test_unknown_control_bad_schema_and_default_mismatch_fail_before_serving():
    function = ast.parse("def demo(value=1):\n    pass").body[0]
    for raw, message in [
        ({"typo": control(True)}, "Unknown"),
        (
            {"value": control({"$ref": "https://invalid.example/schema"})},
            "references",
        ),
        ({"value": control({"type": "string"})}, "default violates"),
    ]:
        with pytest.raises(ValueError, match=message):
            parameter_controls(function, raw)


def test_generator_preserves_policy_and_revision_changes_when_only_policy_changes(
    tmp_path,
):
    import yaml

    catalog = AssetCatalog()
    registry = build_registry(catalog.root / "tools")
    assert registry["tools"]["compute_statistics"]["parameter_controls"][
        "columns"
    ]["editable"]
    # Rebuild a small real deployment with the same Tool source and Skill text.
    root = tmp_path
    (root / "tools").mkdir()
    (root / "skills").mkdir()
    (root / "tools" / "demo.py").write_text(
        "def demo(count=0):\n    return count\n"
    )
    (root / "skills" / "demo.md").write_text("Use demo.")
    (root / "skills" / "skill_index.yaml").write_text(
        yaml.safe_dump(
            {
                "skills": {
                    "demo": {
                        "source": "demo.md",
                        "description": "Demo",
                        "tools": [{"tool": "demo"}],
                    }
                }
            }
        )
    )
    item = {
        "source": "demo.py",
        "function_name": "demo",
        "signature": "demo(count=0)",
        "parameter_controls": {"count": control({"type": "integer"})},
    }

    def save():
        (root / "tools" / "tool_registry.yaml").write_text(
            yaml.safe_dump({"tools": {"demo": item}})
        )

    save()
    before = AssetCatalog(root)
    item["parameter_controls"]["count"]["editable"] = False
    save()
    after = AssetCatalog(root)
    assert (
        before.sources == after.sources and before.revision != after.revision
    )


def test_unresolved_dataset_is_omitted_but_nullable_columns_are_real_values():
    state, catalog = review()
    doc = document()
    with pytest.raises(
        PlanReviewError, match="dataset.*omit unresolved input_values keys"
    ):
        new_review(doc, {"dataset": None}, catalog.metadata, POLICY)
    unresolved = new_review(doc, {}, catalog.metadata, POLICY)
    data = plan_view(unresolved)["inputs"][0]
    assert not data["has_value"] and data["origin"] == "unresolved"
    assert parameter(unresolved, "statistics", "columns")["value"] is None
    assert parameter(unresolved, "statistics", "columns")["has_value"]
    with pytest.raises(
        PlanReviewError, match="Required input is missing: dataset"
    ):
        edit(unresolved, [], action="approve_plan")
    assert not unresolved["consumed"]
    assert edit(
        unresolved,
        [],
        action="approve_plan",
        input_values={"dataset": "default-nce"},
    )["consumed"]
    with pytest.raises(PlanReviewError, match="Unknown proposed input: typo"):
        new_review(doc, {"typo": "default-nce"}, catalog.metadata, POLICY)

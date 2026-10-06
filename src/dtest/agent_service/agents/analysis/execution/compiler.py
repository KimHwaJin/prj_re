"""Compile only approved source and bindings; never run analysis in the Agent."""

import ast
from hashlib import sha256
from copy import deepcopy
from pathlib import Path

from jsonschema_rs import Draft202012Validator

from dtest.contracts.plan_review import canonical, require
from dtest.contracts.workflow_validation import bindings


OBSERVATION_HELPER = """
def _dtest_observe(value, depth=0):
    import math
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            return {"type": "float", "value": None, "non_finite": str(value)}
        return value[:500] if isinstance(value, str) else value
    if type(value).__module__.startswith("numpy") and hasattr(value, "item") and getattr(value, "ndim", 1) == 0:
        return _dtest_observe(value.item(), depth)
    if depth >= 4:
        return {"type": type(value).__name__, "truncated": True}
    if type(value).__name__ == "DataFrame":
        return {"type": "DataFrame", "shape": list(value.shape),
                "columns": [str(x) for x in value.columns[:20]],
                "dtypes": {str(k): str(v) for k,v in value.dtypes.iloc[:20].items()},
                "head": _dtest_observe(value.iloc[:5, :10].to_dict(orient="records"), depth+1),
                "truncated": len(value)>5 or len(value.columns)>20}
    if isinstance(value, dict):
        import itertools
        items = list(itertools.islice(value.items(), 20))
        return {"type": "dict", "items": {str(k)[:120]: _dtest_observe(v, depth+1) for k,v in items},
                "truncated": len(value)>len(items)}
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "items": [_dtest_observe(v, depth+1) for v in value[:10]],
                "truncated": len(value)>10}
    return {"type": type(value).__name__}
"""


def verify_snapshot(snapshot):
    require(
        snapshot["approval_sha256"]
        == sha256(
            canonical(
                {k: v for k, v in snapshot.items() if k != "approval_sha256"}
            ).encode()
        ).hexdigest(),
        "Approved snapshot changed",
    )
    for source in snapshot["tool_sources"].values():
        require(
            sha256(source["code"].encode()).hexdigest()
            == source["code_sha256"],
            "Approved Tool source changed",
        )


def variable(step_id):
    return "_dtest_result_" + sha256(step_id.encode()).hexdigest()[:16]


def resolve(binding, snapshot, decisions, *, evidence=None):
    source = binding["source"]
    if source == "literal":
        return binding["value"]
    if source == "workflow_input":
        name = binding["name"]
        if name not in snapshot["input_values"]:
            require(
                not snapshot["document"]["inputs"][name]["required"],
                "Required input missing",
            )
            raise KeyError(
                name
            )  # Compiler omits absent optional arguments; it does not inject None.
        return (
            snapshot["dataset_bindings"][name]["runtime_path"]
            if name in snapshot["dataset_bindings"]
            else snapshot["input_values"][name]
        )
    if source == "agent_decision":
        require(
            binding["decision_id"] in decisions, "Unresolved Agent decision"
        )
        return decisions[binding["decision_id"]]
    if source == "system_context":
        return snapshot["context"][binding["key"]]
    if source == "step_output":
        require(
            evidence is not None,
            "Full kernel objects cannot be read by the Agent",
        )
        value = evidence[binding["step_id"]]
        for part in binding["selector"]:
            if isinstance(value, dict) and value.get("type") in {
                "dict",
                "list",
                "tuple",
            }:
                value = value["items"]
            value = value[part]
        return value
    raise ValueError("Unknown binding")


def condition_value(condition, snapshot, decisions, evidence=None):
    if "all" in condition:
        return all(
            condition_value(c, snapshot, decisions, evidence)
            for c in condition["all"]
        )
    if "any" in condition:
        return any(
            condition_value(c, snapshot, decisions, evidence)
            for c in condition["any"]
        )
    if "not" in condition:
        return not condition_value(
            condition["not"], snapshot, decisions, evidence
        )
    left, right = (
        resolve(condition[k], snapshot, decisions, evidence=evidence)
        for k in ("left", "right")
    )
    op = condition["op"]
    if op in {"eq", "ne"}:
        require(type(left) is type(right), "Condition types differ")
        return (left == right) if op == "eq" else (left != right)
    if op in {"gt", "gte", "lt", "lte"}:
        require(
            type(left) in {int, float} and type(right) in {int, float},
            "Ordered conditions require numbers",
        )
        import operator

        return {
            "gt": operator.gt,
            "gte": operator.ge,
            "lt": operator.lt,
            "lte": operator.le,
        }[op](left, right)
    require(
        op in {"in", "not_in"} and isinstance(right, (str, list, dict)),
        "Invalid containment condition",
    )
    return (left in right) if op == "in" else (left not in right)


def ready_batch(snapshot, completed, skipped, decisions, evidence=None):
    verify_snapshot(snapshot)
    complete, skip = set(completed), set(skipped)
    available = complete.copy()
    batch = []
    mode = snapshot["execution"]["review_mode"]
    limit = (
        1
        if mode == "every_tool"
        else snapshot["execution"].get("review_interval_tools", 1)
        if mode == "every_n_tools"
        else len(snapshot["steps"])
    )
    # Public sequential plans use an order barrier, separate from required data dependencies.
    ordered = snapshot["document"].get("ordered_call_ids")
    active = {s["id"] for s in snapshot["steps"]}
    order = [key for key in (ordered or []) if key in active]
    # Legacy internal plans retain topological readiness.
    pending = [s for s in snapshot["steps"] if s["id"] not in complete | skip]
    while pending:
        progressed = False
        for step in pending[:]:
            if (
                ordered is not None
                and not set(order[: order.index(step["id"])])
                <= available | skip
            ):
                continue
            if set(step["depends_on"]) & skip:
                skip.add(step["id"])
                pending.remove(step)
                progressed = True
                continue
            if not set(step["depends_on"]) <= available:
                continue
            required = {
                b["decision_id"]
                for b in bindings([step["arguments"], step.get("when")])
                if b["source"] == "agent_decision"
            }
            if not required <= decisions.keys():
                continue
            # A kernel object's observation cannot be fabricated before this Operation runs.
            guards = {
                b["step_id"]
                for b in bindings(step.get("when"))
                if b["source"] == "step_output"
            }
            if not guards <= complete:
                continue
            if step.get("when") and not condition_value(
                step["when"], snapshot, decisions, evidence
            ):
                skip.add(step["id"])
                pending.remove(step)
                progressed = True
                continue
            batch.append(step)
            available.add(step["id"])
            pending.remove(step)
            progressed = True
            if len(batch) >= limit:
                return batch, sorted(skip)
        if not progressed:
            break
    return batch, sorted(skip)


def validate_decisions(snapshot, proposed, completed):
    approved = {d["id"]: d for d in snapshot["document"]["decisions"]}
    require(set(proposed) <= approved.keys(), "Unknown decision")
    for key, value in proposed.items():
        decision = approved[key]
        require(
            set(decision["after_steps"]) <= set(completed),
            "Decision evidence is not complete",
        )
        require(
            Draft202012Validator(decision["output_schema"]).is_valid(value),
            "Decision violates its schema",
        )
    canonical(proposed)
    return deepcopy(proposed)


def compile_steps(snapshot, steps, decisions, start_sequence):
    verify_snapshot(snapshot)
    result = []
    for sequence, step in enumerate(steps, start_sequence):
        source = snapshot["tool_sources"][step["tool_id"]]
        arguments, lineage = [], {}
        for name, binding in step["arguments"].items():
            require(
                name.isidentifier(),
                "Tool argument must be a Python identifier",
            )
            if binding["source"] == "step_output":
                expression = variable(binding["step_id"]) + "".join(
                    f"[{p!r}]" for p in binding["selector"]
                )
                lineage[name] = {
                    "step_id": binding["step_id"],
                    "selector": binding["selector"],
                }
            else:
                try:
                    value = resolve(binding, snapshot, decisions)
                except KeyError:
                    if (
                        binding["source"] == "workflow_input"
                        and binding["name"] not in snapshot["input_values"]
                    ):
                        continue
                    raise
                canonical(value)
                expression = repr(value)
                lineage[name] = (
                    snapshot["input_values"][binding["name"]]
                    if binding["source"] == "workflow_input"
                    else value
                )
            arguments.append(f"{name}={expression}")
        call = f"{variable(step['id'])} = {source['function_name']}({', '.join(arguments)})\n"
        observe = f"print('DTEST_OBSERVATION ' + __import__('json').dumps({{'step_id': {step['id']!r}, 'summary': _dtest_observe({variable(step['id'])})}}, ensure_ascii=False, allow_nan=False))\n"
        code = source["code"] + OBSERVATION_HELPER + "\n" + call + observe
        ast.parse(code)
        result.append(
            {
                "sequence": sequence,
                "payload": {
                    "type": "PYTHON_EXECUTE",
                    "source": {"type": "INLINE", "content": code},
                },
                "lineage": {
                    "skill_name": step["skill_id"],
                    "tool_name": step["tool_id"],
                    "input_parameters": lineage,
                },
            }
        )
    return result


def materialize_steps(root, steps):
    """Immutable content addressed PATH files. Called through retained async I/O."""
    result = deepcopy(steps)
    root = Path(root).resolve()
    directory = root / "agentic-sources"
    directory.mkdir(parents=True, exist_ok=True)
    require(
        directory.resolve().is_relative_to(root),
        "Source directory escapes shared root",
    )
    for step in result:
        content = step["payload"]["source"]["content"].encode()
        checksum = sha256(content).hexdigest()
        relative = f"agentic-sources/{checksum}.py"
        path = root / relative
        tmp = None
        try:
            # O_EXCL publishes exactly once; competing identical writers need no overwrite.
            import os, tempfile

            with tempfile.NamedTemporaryFile(
                dir=path.parent, delete=False
            ) as temp:
                tmp = Path(temp.name)
                temp.write(content)
                temp.flush()
                os.fsync(temp.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                require(
                    not path.is_symlink(),
                    "Immutable source cannot be a symlink",
                )
                require(
                    path.read_bytes() == content,
                    "Immutable source path changed",
                )
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        step["payload"]["source"] = {
            "type": "PATH",
            "path": relative,
            "sha256": checksum,
        }
    return result

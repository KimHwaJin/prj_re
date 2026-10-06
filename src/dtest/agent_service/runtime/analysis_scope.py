"""Interpretation view of approved bindings, separate from result evidence."""

from copy import deepcopy


def execution_scope(state, snapshot, observations, skipped_steps):
    """Describe current bindings, not historical attempts or unobserved kernel values.

    Callers verify the effective snapshot before projecting it. Trusted dataset binding paths,
    system context values and frozen Tool source never enter this view.
    """
    values = snapshot.get("input_values", {})
    decisions = state.get("execution_decisions", {})
    datasets = snapshot.get("dataset_bindings", {})

    def parameter(binding):
        source = binding["source"]
        if source == "literal":
            return {"source": source, "value": deepcopy(binding["value"])}
        if source == "workflow_input":
            name = binding["name"]
            result = {
                "source": source,
                "input_name": name,
                "has_value": name in values,
            }
            if name in datasets:
                result["dataset_id"] = datasets[name]["dataset_id"]
                result["dataset_title"] = datasets[name]["title"]
            elif name in values:
                result["value"] = deepcopy(values[name])
            return result
        if source == "agent_decision":
            key = binding["decision_id"]
            result = {
                "source": source,
                "decision_id": key,
                "has_value": key in decisions,
            }
            if key in decisions:
                result["value"] = deepcopy(decisions[key])
            return result
        if source == "step_output":
            return {
                "source": source,
                "step_id": binding["step_id"],
                "selector": deepcopy(binding["selector"]),
            }
        if source == "system_context":
            return {"source": source, "key": binding["key"]}
        raise ValueError("Unknown approved parameter binding")

    outcomes = {o["step_id"]: o["status"] for o in observations}
    skipped = set(skipped_steps)
    # A repair snapshot may no longer contain the excluded original Steps.
    original = state.get("approved_snapshot") or snapshot
    excluded = set(snapshot.get("excluded_step_ids", []))
    return {
        "plan_id": snapshot.get("plan_id"),
        "plan_revision": snapshot.get("plan_revision"),
        "parameters_scope": "effective_approved_plan_not_attempt_history",
        "steps": [
            {
                "step_id": step["id"],
                "skill_id": step.get("skill_id"),
                "tool_id": step["tool_id"],
                "status": "SKIPPED"
                if step["id"] in skipped
                else outcomes.get(step["id"], "NOT_EXECUTED"),
                "arguments": {
                    name: parameter(binding)
                    for name, binding in step.get("arguments", {}).items()
                },
            }
            for step in snapshot["steps"]
        ],
        "excluded_steps": [
            {
                "step_id": step["id"],
                "skill_id": step.get("skill_id"),
                "tool_id": step["tool_id"],
                "status": "EXCLUDED_BY_USER",
            }
            for step in original["document"].get("steps", [])
            if step["id"] in excluded
        ],
    }

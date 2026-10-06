"""Validate a pending decision form before admitting a user resume."""

from dtest.contracts.plan_interaction import DecisionAction
from dtest.contracts.plan_review import require
from jsonschema_rs import Draft202012Validator


def validate_decision_action(review, raw):
    action = DecisionAction.model_validate(raw)
    require(
        str(action.interaction_id) == review["interaction_id"]
        and action.revision == review["revision"],
        "Stale decision form",
    )
    fields = {f["decision_id"]: f for f in review["payload"]["decisions"]}
    require(
        set(action.values) == fields.keys(),
        "Confirm all and only pending decisions",
    )
    for key, value in action.values.items():
        require(
            Draft202012Validator(fields[key]["value_schema"]).is_valid(value),
            "Decision value violates its schema",
        )
    return action.values

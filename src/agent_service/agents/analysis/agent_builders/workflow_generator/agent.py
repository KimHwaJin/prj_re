"""Declare workflow_generator: explicit output contract and middleware policies."""
from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware
from ...schemas.workflows.workflow_plan_format import WorkflowPlanOutput
from .._prompts import load_prompt

def build_agent(model, *, structured_output_mode="prompt_json"):
    return build_role_agent(
        model,
        name="workflow_generator",
        system_prompt=load_prompt(__package__) + "\n\nUse the provided workflow_resources to produce the Workflow plan.",
        tools=[],
        middleware=[ProjectPromptMiddleware()],
        output_type=WorkflowPlanOutput,
        structured_output_mode=structured_output_mode,
        max_validation_attempts=1,
        decode=json_output(WorkflowPlanOutput),
    )

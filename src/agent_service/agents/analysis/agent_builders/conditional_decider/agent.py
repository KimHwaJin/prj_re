"""Declare conditional_decider: explicit output contract and middleware policies."""
from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware
from ...schemas.agents.orchestration_schema import ConditionalDecisionOutput
from .._prompts import load_prompt

def build_agent(model, *, structured_output_mode="prompt_json"):
    return build_role_agent(
        model,
        name="conditional_decider",
        system_prompt=load_prompt(__package__),
        tools=[],
        middleware=[ProjectPromptMiddleware()],
        output_type=ConditionalDecisionOutput,
        structured_output_mode=structured_output_mode,
        max_validation_attempts=3,
        decode=json_output(ConditionalDecisionOutput),
    )

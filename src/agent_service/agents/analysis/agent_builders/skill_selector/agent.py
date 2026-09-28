"""Declare skill_selector: explicit output contract and middleware policies."""
from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware
from ...schemas.agents.workflow_generator_schema import SkillSelectionOutput
from .._prompts import load_prompt
from ...tools.catalog import load_workflow_catalog_context

def build_agent(model, *, structured_output_mode="prompt_json"):
    return build_role_agent(
        model,
        name="skill_selector",
        system_prompt=load_prompt(__package__) + "\n\n" + load_workflow_catalog_context(),
        tools=[],
        middleware=[ProjectPromptMiddleware()],
        output_type=SkillSelectionOutput,
        structured_output_mode=structured_output_mode,
        max_validation_attempts=3,
        decode=json_output(SkillSelectionOutput),
    )

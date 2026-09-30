"""Decide approved deferred parameters from actual text observations."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from agent_service.factory import build_role_agent,json_output
from agent_service.middleware import ProjectPromptMiddleware
from .._prompts import load_prompt


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    decision_id: str
    value: Any
    reason: str = Field(min_length=1,max_length=4000)
    evidence_steps: list[str]


class ReviewResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    choices: list[Choice] = Field(max_length=100)
    needs_user_input: bool
    message: str = Field(min_length=1,max_length=6000)


def build_agent(model, *, structured_output_mode='prompt_json'):
    return build_role_agent(model,name='analysis_execution_review',system_prompt=load_prompt(__package__),
        tools=[],middleware=[ProjectPromptMiddleware()],output_type=ReviewResponse,decode=json_output(ReviewResponse),
        structured_output_mode=structured_output_mode,max_validation_attempts=2)

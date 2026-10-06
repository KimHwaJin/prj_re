"""Decide approved deferred parameters from actual text observations."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
import json
from jsonschema_rs import Draft202012Validator
from langchain_core.messages import HumanMessage

from dtest.agent_service.factory import build_role_agent,json_output
from dtest.agent_service.middleware import ProjectPromptMiddleware
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


def validate_choices(response, request):
    """Retry semantic contract errors inside create_agent, using original evidence only."""
    payload=json.loads(next(m.content for m in request.messages if isinstance(m,HumanMessage)))
    pending={d['id']:d for d in payload['pending_decisions']}
    successful={o['step_id'] for o in payload['observations'] if o['status']=='SUCCEEDED' and not o.get('incomplete',False)}
    ids=[c.decision_id for c in response.choices]
    if len(ids)!=len(set(ids)) or not set(ids)<=pending.keys():
        raise ValueError('Use each pending decision_id at most once. Allowed decision IDs: '+json.dumps(sorted(pending)))
    for choice in response.choices:
        decision=pending[choice.decision_id]
        if not set(choice.evidence_steps)<=successful or not set(decision['after_steps'])<=set(choice.evidence_steps):
            raise ValueError('evidence_steps must use exact successful observation.step_id strings, not Tool names. '
                'Allowed Step IDs: '+json.dumps(sorted(successful))+'. Required for '+choice.decision_id+': '+json.dumps(decision['after_steps']))
        if not Draft202012Validator(decision['output_schema']).is_valid(choice.value):
            raise ValueError('Value for '+choice.decision_id+' must satisfy its approved output_schema: '+json.dumps(decision['output_schema']))
    if not response.needs_user_input and set(ids)!=pending.keys():
        raise ValueError('Resolve all pending decision IDs or set needs_user_input=true and explain missing evidence')


def build_agent(model, *, structured_output_mode='prompt_json', store=None):
    return build_role_agent(model,name='analysis_execution_review',system_prompt=load_prompt(__package__),
        tools=[],middleware=[ProjectPromptMiddleware()],output_type=ReviewResponse,decode=json_output(ReviewResponse),
        structured_output_mode=structured_output_mode,max_validation_attempts=2,validate_response=validate_choices, store=store)

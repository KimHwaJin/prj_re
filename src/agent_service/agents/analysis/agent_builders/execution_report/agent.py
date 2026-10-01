"""Evidence-grounded Markdown reports, generated through create_agent middleware."""
from pydantic import BaseModel,ConfigDict,Field
import json
from langchain_core.messages import HumanMessage
from agent_service.factory import build_role_agent,json_output
from agent_service.middleware import ProjectPromptMiddleware
from .._prompts import load_prompt
from ...execution.grounding import validate_interpretation


class ReportResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    markdown: str = Field(min_length=1,max_length=24000)
    evidence_steps: list[str]


def validate_evidence(response, request):
    # Original role payload is trusted; later retry messages are not new evidence.
    payload = json.loads(next(m.content for m in request.messages if isinstance(m, HumanMessage)))
    allowed = {o['step_id'] for o in payload['observations'] if o['status']=='SUCCEEDED'}
    if not set(response.evidence_steps) <= allowed:
        raise ValueError('evidence_steps must use the exact successful observation.step_id values. '
                         'Allowed IDs: '+json.dumps(sorted(allowed)))
    validate_interpretation(response.markdown, payload['observations'])


def build_agent(model, *, structured_output_mode='prompt_json'):
    return build_role_agent(model,name='analysis_execution_report',system_prompt=load_prompt(__package__),
        tools=[],middleware=[ProjectPromptMiddleware()],output_type=ReportResponse,decode=json_output(ReportResponse),
        structured_output_mode=structured_output_mode,max_validation_attempts=2,
        validate_response=validate_evidence)

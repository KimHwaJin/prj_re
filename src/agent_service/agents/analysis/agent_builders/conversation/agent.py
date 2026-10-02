"""One create_agent loop for answers, clarification and catalogue-based plans."""
import json
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware, SessionAnalysisMiddleware
from agent_service.middleware.discovery import MetadataDiscoveryMiddleware
from agent_service.middleware.planning_contract import PlanningContractMiddleware
from service_contracts.plan_review import new_review
from service_contracts.workflow_validation import workflow_schema
from .._prompts import load_prompt
from ...execution.grounding import AnswerGrounding, grounded_message, compact_evidence_view


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    definition: dict
    input_values: dict = Field(default_factory=dict)


def reply_schema(catalog, max_candidates, repair_limit=4,repair_attempts=3):
    class Reply(BaseModel):
        model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
        kind: Literal['answer', 'planning', 'plans']
        message: str = Field(min_length=1, max_length=12000, description=
            'For answer with analysis grounding: qualitative interpretation ONLY, no digit characters, numeric values, percentages or numbered headings. '
            'Do not describe row/column counts or IQR fractions numerically. Exact values appear in the server-rendered table from fact_ids. '
            'For plans or unrelated general FAQ, ordinary text is allowed.')
        grounding: AnswerGrounding | None = None
        plans: list[Proposal] = Field(default_factory=list, max_length=max_candidates)
        skill_ids: list[str] = Field(default_factory=list, max_length=20)

        @model_validator(mode='after')
        def valid(self):
            if (self.kind == 'plans') != bool(self.plans):
                raise ValueError('plans kind requires plans; answer kind forbids plans')
            if self.kind == 'planning':
                if self.grounding is not None or not self.skill_ids or len(self.skill_ids) != len(set(self.skill_ids)) or not set(self.skill_ids) <= set(catalog.metadata['skills']):
                    raise ValueError('Planning selection requires distinct registered skill_ids and grounding=null')
            elif self.skill_ids:
                raise ValueError('Only planning selection carries skill_ids')
            for proposal in self.plans:
                policy = {'allowed_modes': ['MULTI'] if proposal.definition.get('decisions') or any('when' in s for s in proposal.definition.get('steps', [])) else ['SINGLE', 'MULTI'],
                          'repair_level_limit': repair_limit, 'max_repair_attempts_limit': repair_attempts}
                review = new_review(proposal.definition, proposal.input_values, catalog.metadata, policy)
                if review['document']['execution']['max_repair_attempts'] > repair_attempts:
                    raise ValueError('Repair attempts exceed the service limit')
                # Paths are resolved by the service, never fabricated from chat.
                for step in proposal.definition['steps']:
                    if step['tool_id'] == 'data_load':
                        binding = step['arguments'].get('parquet_path', {})
                        field = proposal.definition['inputs'].get(binding.get('name'), {})
                        if binding.get('source') != 'workflow_input' or field.get('kind') != 'data_reference':
                            raise ValueError('data_load.parquet_path must reference a data_reference Workflow input')
            return self
    return Reply


def build_agent(model, catalog, *, max_candidates=5, discovery_max_rounds=4, structured_output_mode='prompt_json',repair_limit=4,repair_attempts=3,session_context_max_chars=16000):
    schema = reply_schema(catalog, max_candidates,repair_limit,repair_attempts)
    prompt = load_prompt(__package__)
    planning_prompt = files(__package__).joinpath('planning_prompt.md').read_text(encoding='utf-8') + '\nWorkflow definition JSON Schema:\n' + json.dumps(workflow_schema(), ensure_ascii=False)
    planning = PlanningContractMiddleware(planning_prompt, schema=schema)
    def validate_response(reply, request):
        if reply.kind == 'plans' and not planning.discovered(request.messages):
            raise ValueError('Plan proposals require Skill/Tool discovery first. Return kind=planning with relevant skill_ids before returning plans.')
        if reply.kind == 'planning':
            raise ValueError('Skill metadata is already available; return a final answer or plans, not another planning selection')
        grounded_message(reply, request.runtime.context, max_chars=session_context_max_chars)
    return build_role_agent(model, name='analysis_conversation', system_prompt=prompt,
                            tools=catalog.metadata_tools(), middleware=[ProjectPromptMiddleware(), planning, SessionAnalysisMiddleware(max_chars=session_context_max_chars, evidence_view=compact_evidence_view), MetadataDiscoveryMiddleware(max_rounds=discovery_max_rounds)],
                            output_type=schema, decode=json_output(schema),
                            structured_output_mode=structured_output_mode, max_validation_attempts=3, validate_response=validate_response)

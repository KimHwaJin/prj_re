"""One create_agent loop for answers, clarification and catalogue-based plans."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware
from agent_service.middleware.discovery import MetadataDiscoveryMiddleware
from service_contracts.plan_review import new_review
from service_contracts.workflow_validation import workflow_schema
from .._prompts import load_prompt


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    definition: dict
    input_values: dict = Field(default_factory=dict)


def reply_schema(catalog, max_candidates):
    class Reply(BaseModel):
        model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
        kind: Literal['answer', 'plans']
        message: str = Field(min_length=1, max_length=12000)
        plans: list[Proposal] = Field(default_factory=list, max_length=max_candidates)

        @model_validator(mode='after')
        def valid(self):
            if (self.kind == 'plans') != bool(self.plans):
                raise ValueError('plans kind requires plans; answer kind forbids plans')
            for proposal in self.plans:
                policy = {'allowed_modes': ['MULTI'] if proposal.definition.get('decisions') or any('when' in s for s in proposal.definition.get('steps', [])) else ['SINGLE', 'MULTI'],
                          'repair_level_limit': 4, 'max_repair_attempts_limit': 3}
                review = new_review(proposal.definition, proposal.input_values, catalog.metadata, policy)
                if review['document']['execution']['max_repair_attempts'] > 3:
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


def build_agent(model, catalog, *, max_candidates=5, discovery_max_rounds=4, structured_output_mode='prompt_json'):
    schema = reply_schema(catalog, max_candidates)
    prompt = load_prompt(__package__) + '\nWorkflow definition JSON Schema:\n' + json.dumps(workflow_schema(), ensure_ascii=False)
    return build_role_agent(model, name='analysis_conversation', system_prompt=prompt,
                            tools=catalog.metadata_tools(), middleware=[ProjectPromptMiddleware(), MetadataDiscoveryMiddleware(max_rounds=discovery_max_rounds)],
                            output_type=schema, decode=json_output(schema),
                            structured_output_mode=structured_output_mode, max_validation_attempts=3)

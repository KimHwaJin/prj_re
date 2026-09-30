"""A stateless create_agent proposes repairs; deterministic policy decides execution."""
from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware
from agent_service.middleware.discovery import MetadataDiscoveryMiddleware
from service_contracts.execution_repair import RepairResponse
from .._prompts import load_prompt


def build_agent(model, catalog=None, *, enable_discovery=True, discovery_max_rounds=4, structured_output_mode='prompt_json', validate_response=None):
    return build_role_agent(model, name='analysis_execution_repair', system_prompt=load_prompt(__package__),
        tools=catalog.metadata_tools() if catalog and enable_discovery else [], middleware=[ProjectPromptMiddleware(), MetadataDiscoveryMiddleware(max_rounds=discovery_max_rounds,
            final_instruction='Return exactly the RepairResponse JSON schema: can_repair, summary, reason, evidence_steps and the proposed changes. Do not return a conversation Reply or Workflow candidates.')], output_type=RepairResponse,
        decode=json_output(RepairResponse), structured_output_mode=structured_output_mode,
        max_validation_attempts=2, validate_response=validate_response)

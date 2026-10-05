"""One create_agent loop for answers, clarification and catalogue-based plans."""
import json
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, PrivateAttr, create_model

from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware, SessionAnalysisMiddleware
from agent_service.middleware.discovery import MetadataDiscoveryMiddleware
from agent_service.middleware.planning_contract import PlanningContractMiddleware
from service_contracts.project_memory import MemoryProposal, MemoryLimits
from agent_service.middleware.project_memory import ProjectMemoryMiddleware
from agent_service.runtime.project_memory import validate_memory_proposals, extract_memory_changes
from service_contracts.plan_review import new_review
from service_contracts.workflow_validation import workflow_schema
from .._prompts import load_prompt
from ...execution.grounding import AnswerGrounding, grounded_message, compact_evidence_view


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    definition: dict | None = None
    workflow_id: str | None = Field(default=None, description="검색 결과에서 선택한 Workflow ID. 추천일 때 definition은 생략합니다.")
    _catalog_reference: dict | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def exclusive_definition(self):
        if (self.definition is None) == (self.workflow_id is None):
            raise ValueError("Provide exactly one of definition or retrieved workflow_id")
        return self
    input_values: dict = Field(default_factory=dict)


def reply_schema(catalog, max_candidates, repair_limit=4,repair_attempts=3, memory_limits=None):
    limits = memory_limits or MemoryLimits()
    proposal_type = create_model('ProjectMemoryProposal', __base__=MemoryProposal,
        content=(str,Field(min_length=1,max_length=limits.patch_max_chars,description='Complete replacement section body; preserve existing valid information, no unsupported claims.')),
        quote=(str,Field(min_length=1,max_length=limits.patch_max_chars,description='Exact CURRENT user quote supporting durable project sharing.')))
    class Reply(BaseModel):
        model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
        kind: Literal['answer', 'planning', 'plans']
        message: str = Field(min_length=1, max_length=12000, description=
            'For a report-writing/revision answer, include the complete Markdown report in message, not just an acknowledgement of completion. '
            'For answer with analysis grounding: qualitative interpretation ONLY, no digit characters, numeric values, percentages or numbered headings. '
            'Do not describe observed counts or fractions numerically. Exact values appear in the server-rendered table from fact_ids. '
            'For plans or unrelated general FAQ, ordinary text is allowed.')
        memory_updates: list[proposal_type] = Field(default_factory=list, max_length=limits.max_updates, description="Only durable project background/preferences or explicit remember requests. Normalize briefly, preserve exact CURRENT quote and intent. Never session-only requests, findings, data or paths. Echo the document version and exact full old_text. Edit only visible/editable sections; empty when automatic_write=false or no change.")
        _memory_result: dict | None = PrivateAttr(default=None)
        grounding: AnswerGrounding | None = None
        plans: list[Proposal] = Field(default_factory=list, max_length=max_candidates)
        skill_ids: list[str] = Field(default_factory=list, max_length=20)
        planning_scope: Literal["incremental", "end_to_end"] = Field(default="incremental", description="planning일 때만: 전체 E2E 분석이면 end_to_end, 부분 분석/추가는 incremental. 최종 답변은 기본값 incremental.")

        @model_validator(mode='after')
        def valid(self):
            if (self.kind == 'plans') != bool(self.plans):
                raise ValueError('plans kind requires plans; answer kind forbids plans')
            if self.kind == 'planning' and self.memory_updates:
                raise ValueError('Planning selection cannot contain memory updates')
            if self.kind == 'planning':
                if self.grounding is not None or not self.skill_ids or len(self.skill_ids) != len(set(self.skill_ids)) or not set(self.skill_ids) <= set(catalog.metadata['skills']):
                    raise ValueError('Planning selection requires distinct registered skill_ids and grounding=null')
            elif self.skill_ids:
                raise ValueError('Only planning selection carries skill_ids')
            for proposal in self.plans:
                if proposal.workflow_id is not None:
                    continue  # server resolves/validates an immutable retrieved snapshot
                policy = {'allowed_modes': ['MULTI'] if proposal.definition.get('decisions') or any('when' in s for s in proposal.definition.get('steps', [])) else ['SINGLE', 'MULTI'],
                          'repair_level_limit': repair_limit, 'max_repair_attempts_limit': repair_attempts}
                review = new_review(proposal.definition, proposal.input_values, catalog.metadata, policy)
                if review['document']['execution']['max_repair_attempts'] > repair_attempts:
                    raise ValueError('Repair attempts exceed the service limit')
            return self
    return Reply


def build_agent(model, catalog, *, max_candidates=5, discovery_max_rounds=4, structured_output_mode='prompt_json',repair_limit=4,repair_attempts=3,session_context_max_chars=16000,store=None,memory_limits=None,workflow_retriever=None,workflow_context_max_chars=64000):
    schema = reply_schema(catalog, max_candidates,repair_limit,repair_attempts,memory_limits)
    prompt = load_prompt(__package__)
    planning_prompt = files(__package__).joinpath('planning_prompt.md').read_text(encoding='utf-8') + '\nWorkflow definition JSON Schema:\n' + json.dumps(workflow_schema(), ensure_ascii=False)
    planning = PlanningContractMiddleware(planning_prompt, schema=schema, workflow_search_enabled=workflow_retriever is not None)
    from ...planning.recommendations import workflow_search_tool, resolve_recommendations
    tools = catalog.metadata_tools()
    if workflow_retriever is not None:
        tools.append(workflow_search_tool(workflow_retriever, catalog, context_max_chars=workflow_context_max_chars))
    def validate_response(reply, request):
        if reply.kind == 'plans' and not planning.discovered(request.messages):
            raise ValueError('Plan proposals require Skill/Tool discovery first. Return kind=planning with relevant skill_ids before returning plans.')
        if reply.kind == 'planning':
            raise ValueError('Skill metadata is already available; return a final answer or plans, not another planning selection')
        resolve_recommendations(reply, request.messages, catalog, repair_limit=repair_limit, repair_attempts=repair_attempts)
        grounded_message(reply, request.runtime.context, max_chars=session_context_max_chars)
        validate_memory_proposals(reply, request)
    def decode(result):
        reply = json_output(schema)(result)
        resolve_recommendations(reply, result["messages"], catalog, repair_limit=repair_limit, repair_attempts=repair_attempts)
        reply._memory_result = result.get("project_memory_write_result")
        return reply
    memory_policy = ProjectMemoryMiddleware(extract_changes=lambda result: extract_memory_changes(schema, result))
    return build_role_agent(model, name='analysis_conversation', system_prompt=prompt,
                            tools=tools, middleware=[ProjectPromptMiddleware(), memory_policy, planning, SessionAnalysisMiddleware(max_chars=session_context_max_chars, evidence_view=compact_evidence_view), MetadataDiscoveryMiddleware(max_rounds=discovery_max_rounds)],
                            output_type=schema, decode=decode,
                            structured_output_mode=structured_output_mode, max_validation_attempts=3, validate_response=validate_response, store=store)

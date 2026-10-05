"""Private execution-local proposals. Registered Workflow validation stays unchanged."""
from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator
from service_contracts.plan_interaction import StrictModel
from service_contracts.plan_review import new_review, patch_review, require, canonical
from service_contracts.workflow_validation import workflow_schema
from service_contracts.tool_bindings import inherit_parameter_policy
from agent_service.agents.analysis.execution.sources import source_info


class CodeLine(StrictModel):
    indent: int = Field(ge=1, le=16, strict=True)
    text: str = Field(min_length=1, max_length=8000, pattern=r'^[^\r\n]+$')


class LocalFunction(StrictModel):
    step_id: str
    header: str = Field(min_length=1, max_length=4000, pattern=r'^def [^\r\n]+:$')
    body: list[CodeLine] = Field(min_length=1, max_length=1200)
    origin_tool_id: str | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @property
    def code(self):
        return self.header + '\n' + '\n'.join('    ' * line.indent + line.text for line in self.body) + '\n'

    @code.setter
    def code(self, value):
        parsed = self.from_code(step_id=self.step_id, code=value, reason=self.reason, origin_tool_id=self.origin_tool_id)
        self.header, self.body = parsed.header, parsed.body

    @classmethod
    def from_code(cls, *, step_id, code, reason, origin_tool_id=None):
        # Internal fixture/adapter convenience only; model output uses typed lines.
        import ast
        normalized=ast.unparse(ast.parse(code)).splitlines()
        body=[]
        for line in normalized[1:]:
            if line.strip():
                spaces=len(line)-len(line.lstrip(' '))
                body.append({'indent':spaces//4,'text':line.lstrip(' ')})
        return cls(step_id=step_id,header=normalized[0],body=body,reason=reason,origin_tool_id=origin_tool_id)


class BindingChange(StrictModel):
    name: str
    binding: dict


class StepRevision(StrictModel):
    step_id: str
    skill_id: str | None = None
    tool_id: str | None = None
    description: str | None = None
    depends_on: list[str] | None = None
    parameter_changes: list[BindingChange] = Field(default_factory=list, max_length=100)
    removed_parameters: list[str] = Field(default_factory=list, max_length=100)


class RevisionProposal(StrictModel):
    definition: dict | None = None
    base_plan_id: str | None = None
    patches: list[StepRevision] = Field(default_factory=list, max_length=64)
    excluded_step_ids: list[str] = Field(default_factory=list, max_length=100)
    goal: str | None = Field(default=None, min_length=1, max_length=12000)
    input_values: dict = Field(default_factory=dict)
    functions: list[LocalFunction] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def shape(self):
        if (self.definition is None) == (self.base_plan_id is None):
            raise ValueError('Choose a complete definition OR a current base_plan_id')
        if self.definition is not None and (self.patches or self.goal is not None or self.excluded_step_ids):
            raise ValueError('Complete definitions cannot also contain base-plan patches')
        return self


class RevisionReply(StrictModel):
    kind: Literal['plans', 'clarification']
    message: str = Field(min_length=1, max_length=12000)
    plans: list[RevisionProposal] = Field(default_factory=list, max_length=20)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        # dict alone becomes an unconstrained object to native JSON decoders.
        # Hoist the existing Workflow definitions under unique names; preserve
        # recursive condition references instead of flattening/copying schemas.
        schema = super().model_json_schema(*args, **kwargs)
        workflow = deepcopy(workflow_schema())
        workflow.pop('$id', None)  # Nested resource IDs would reset the rebased $ref root.
        workflow.pop('$schema', None)
        def rebase(value):
            if isinstance(value, dict):
                return {k: '#/$defs/workflow_' + v.removeprefix('#/$defs/')
                        if k == '$ref' and isinstance(v, str) and v.startswith('#/$defs/') else rebase(v)
                        for k, v in value.items() if k not in {'uniqueItems','propertyNames','pattern','$comment'}}
            if isinstance(value, list):
                return [rebase(v) for v in value]
            return value
        definitions = workflow.pop('$defs', {})
        schema['$defs'].update({'workflow_' + k: rebase(v) for k, v in definitions.items()})
        schema['$defs']['ExecutionPlanDefinition'] = rebase(workflow)
        schema['$defs']['RevisionProposal']['properties']['definition'] = {'anyOf':[{'$ref':'#/$defs/ExecutionPlanDefinition'},{'type':'null'}]}
        schema['$defs']['BindingChange']['properties']['binding'] = {'$ref':'#/$defs/workflow_binding'}
        def remove_generation_patterns(value):
            if isinstance(value,dict):return {k:remove_generation_patterns(v) for k,v in value.items() if k!='pattern'}
            if isinstance(value,list):return [remove_generation_patterns(v) for v in value]
            return value
        return remove_generation_patterns(schema)

    @model_validator(mode='after')
    def shape(self):
        if (self.kind == 'plans') != bool(self.plans):
            raise ValueError('plans requires candidates; clarification cannot contain plans')
        return self


def prepare_review(proposal, runtime, state):
    """Validate without executing source or modifying the deployed catalogue."""
    base = None
    if proposal.base_plan_id:
        candidates = state.get('reviews') or state.get('planning_previous_reviews', [])
        base = next((r for r in candidates if r['plan_id'] == proposal.base_plan_id), None)
        require(base is not None, 'Unknown or retired base plan')
        document = deepcopy(base['document'])
        seen = set()
        for patch in proposal.patches:
            require(patch.step_id not in seen, 'Duplicate Step revision')
            seen.add(patch.step_id)
            step = next((s for s in document['steps'] if s['id'] == patch.step_id), None)
            if step is None:
                require(patch.skill_id is not None and patch.tool_id is not None and patch.description is not None and patch.depends_on is not None,
                        'New Step must declare Skill/Tool/description/dependencies')
                step = {'id':patch.step_id,'arguments':{}}
                document['steps'].append(step)
            if patch.tool_id is not None and patch.tool_id != step.get('tool_id'):
                step.pop('parameter_controls', None)
            for key in ('skill_id','tool_id','description','depends_on'):
                value = getattr(patch,key)
                if value is not None:step[key]=deepcopy(value)
            names = [v.name for v in patch.parameter_changes]
            require(len(names)==len(set(names)) and not set(names)&set(patch.removed_parameters), 'Duplicate/conflicting parameter revision')
            for name in patch.removed_parameters:
                require(name in step['arguments'], 'Unknown removed parameter')
                step['arguments'].pop(name)
                step.get('parameter_controls',{}).pop(name,None)
            for change in patch.parameter_changes:step['arguments'][change.name]=deepcopy(change.binding)
        if proposal.goal is not None:document['goal']=proposal.goal
        values = {**base['input_values'],**proposal.input_values}
    else:
        document = deepcopy(proposal.definition)
        values = proposal.input_values
    metadata = deepcopy(runtime.catalog.metadata)
    sources = {}
    custom_steps = {}
    require(len(canonical(proposal.model_dump()).encode()) <= 300000, 'Execution-local proposal is too large')
    if proposal.functions or any(s.get('tool_id','').startswith('custom.') for s in document.get('steps',[])):
        require(state.get('planning_revision_count', 0) > 0 and runtime.settings.agent_free_plan_enabled,
                'Free code is only allowed after a user revision and when enabled')
    for function in proposal.functions:
        require(bool(function.reason.strip()), 'Execution-local source must explain why registered Tools cannot satisfy the request')
        require(function.step_id not in custom_steps, 'Duplicate execution-local Step source')
        step = next((s for s in document.get('steps', []) if s.get('id') == function.step_id), None)
        require(step is not None and step.get('tool_id', '').startswith('custom.'), 'Local source requires a custom.* Step')
        skill = step.get('skill_id')
        require(skill in metadata['skills'], 'Execution-local functions require a registered Skill')
        tool = step['tool_id']
        require(tool not in metadata['tools'] and tool not in sources, 'Execution-local Tool ID must be unique')
        expected = None
        if function.origin_tool_id:
            require(function.origin_tool_id in metadata['skills'][skill]['tools'], 'Origin Tool must belong to the registered Skill')
            expected = runtime.catalog.sources[function.origin_tool_id]
        source, info = source_info(function.code, expected=expected)
        if function.origin_tool_id:
            require(source['code_sha256'] != expected['code_sha256'], 'Unmodified registered Tool must use its registered ID')
            source['origin_tool_id'] = function.origin_tool_id
            info = inherit_parameter_policy(info, metadata['tools'][function.origin_tool_id])
        sources[tool] = source
        metadata['tools'][tool] = info
        metadata['skills'][skill]['tools'].append(tool)
        custom_steps[function.step_id] = function.reason
    if base:
        # A patch carries unchanged functions forward from this reviewed candidate
        # only. Never resolve custom identifiers from another Run or the registry.
        for step in document.get('steps', []):
            tool = step.get('tool_id', '')
            if not tool.startswith('custom.') or step['id'] in custom_steps:
                continue
            prior = next((s for s in base['document']['steps'] if s['id'] == step['id'] and s['tool_id'] == tool), None)
            previous_source = base.get('local_sources', {}).get(tool)
            require(prior is not None and previous_source is not None, 'Unknown execution-local source in base plan')
            require(step['skill_id'] == prior['skill_id'] and tool not in metadata['tools'] and tool not in sources,
                    'Inherited execution-local Tool must retain its registered Skill and unique ID')
            _, info = source_info(previous_source['code'], expected=previous_source)
            info = inherit_parameter_policy(info, base['catalog']['tools'][tool])
            sources[tool] = deepcopy(previous_source)
            metadata['tools'][tool] = info
            metadata['skills'][step['skill_id']]['tools'].append(tool)
            custom_steps[step['id']] = base['local_reasons'][step['id']]
    require({s['id'] for s in document.get('steps', []) if s.get('tool_id', '').startswith('custom.')} == set(custom_steps),
            'Every custom Step must have exactly one local source')
    for step in document.get('steps', []):
        for binding in step.get('arguments', {}).values():
            require(binding.get('source') != 'literal' or not isinstance(binding.get('value'), str) or
                    not binding['value'].startswith('/workspace/'), 'Runtime file paths must use trusted inputs/context')
    policy = {'allowed_modes': ['MULTI'] if document.get('decisions') or any('when' in s for s in document.get('steps', [])) else ['SINGLE', 'MULTI'],
              'repair_level_limit': runtime.settings.agent_repair_level_limit,
              'max_repair_attempts_limit': runtime.settings.agent_max_repair_attempts,
              'default_repair_level': runtime.settings.agent_repair_level,
              'default_repair_attempts': runtime.settings.agent_max_repair_attempts}
    review = new_review(document, values, metadata, policy)
    if base:
        for key,value in base['input_values'].items():
            if review['input_values'].get(key)==value:
                review['input_origins'][key]=base['input_origins'].get(key,'agent')
        for step in review['document']['steps']:
            previous = next((s for s in base['document']['steps'] if s['id'] == step['id']), None)
            if previous and previous['tool_id'] == step['tool_id']:
                for name, binding in step['arguments'].items():
                    if binding == previous['arguments'].get(name):
                        review['parameter_origins'][step['id']][name] = base.get('parameter_origins', {}).get(step['id'], {}).get(name, 'agent' if binding['source'] == 'literal' else 'unresolved')
        review['user_actions'].append({'action':'agent_revision','base_plan_id':base['plan_id'],'feedback_turn':state['planning_revision_count']})
        review['excluded_step_ids']=sorted(set(base.get('excluded_step_ids',[]))|set(proposal.excluded_step_ids))
    review = patch_review(review, {'action': 'edit_plan', 'plan_id': review['plan_id'], 'plan_revision': 1},
                 datasets=runtime.datasets, context=state)
    review.update(local_sources=sources, execution_kind='free_code' if sources else 'registered',
                  workflow_eligible=not bool(sources), local_reasons=custom_steps, approval_mode='user')
    from service_contracts.plan_projection import plan_view
    public = canonical(plan_view(review)) + canonical(custom_steps)
    require('```' not in public and 'def ' not in public and '/workspace/' not in public,
            'Public plan descriptions/parameters must not contain source or runtime paths')
    return review


def validate_revision_reply(reply, runtime, state):
    require(len(reply.plans) <= runtime.settings.max_plan_candidates, 'Too many plan candidates')
    require('```' not in reply.message and 'def ' not in reply.message and '/workspace/' not in reply.message,
            'Planning messages must not expose Python source or runtime paths')
    return [prepare_review(proposal, runtime, state) for proposal in reply.plans]

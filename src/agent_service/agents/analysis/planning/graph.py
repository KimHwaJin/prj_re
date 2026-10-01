"""Durable conversation → candidate plans → human edits → frozen approval."""
from datetime import datetime, timezone
from typing import TypedDict, Any
from uuid import uuid4

from langgraph.graph import StateGraph, START, END

from agent_service.context import AgentContext
from agent_service.runtime.user_resume import record_user_resume, user_interrupt
from service_contracts.plan_review import new_review, patch_review, freeze_approval, visible_datasets, PlanReviewError
from service_contracts.plan_projection import plan_view
from service_contracts.plan_interaction import validate_plan_revision
from agent_service.agents.analysis.planning.proposals import validate_revision_reply
from agent_service.middleware.prompt_json import StructuredResponseError
from agent_service.runtime.session_analysis import analysis_for_owner


RUNTIME_VERSION = 'agentic-planning-v1'


class PlanningState(TypedDict, total=False):
    agent_runtime: str
    user_id: str
    project_id: str
    session_id: str
    run_id: str
    task_id: str
    public_run_id: str
    agent_run_id: str
    thread_id: str
    request_id: str
    user_request: str
    trigger_message_id: str
    project_system_prompt: str
    project_prompt_version: int
    model_selection: dict
    initial_request_identity: dict | None
    initial_request_receipt: dict | None
    user_resume_receipt: dict | None
    history: list[dict]
    last_analysis_context: dict | None
    public_events: list[dict]
    reviews: list[dict]
    plan_views: list[dict]
    asset_revision: str
    interaction_id: str
    interaction_revision: int
    interaction_data: dict | None
    review_action: dict | None
    review_error: str | None
    approved_snapshot: dict | None
    routing_result: dict
    final_response: dict | None
    kernel_profile: str
    dataset_output_dir: str
    execution_id: str | None
    executor_version: int
    executor_operation_number: int
    executor_operation_id: str
    executor_wait_phase: str
    execution_command: dict
    execution_phase: str
    submitted_steps: list[dict]
    next_step_sequence: int
    completed_steps: list[str]
    skipped_steps: list[str]
    execution_decisions: dict
    pending_decisions: list[dict]
    decision_review: dict | None
    execution_review_validation_error: str | None
    observations: list[dict]
    execution_status: str
    execution_error: dict | str | None
    analysis_failure: bool
    terminal_event_seen: bool
    report_status: str
    execution_snapshot: dict | None
    failed_step_ids: list[str]
    repair_attempts: int
    repair_max_attempts: int
    repair_authorized_level: int
    repair_candidate: dict | None
    repair_review: dict | None
    repair_action: str
    repair_history: list[dict]
    repair_stop_reason: str | None
    repair_validation_error: str | None
    planning_revision_count: int
    planning_feedback: list[dict]
    planning_previous_reviews: list[dict]
    planning_question: str | None
    planning_validation_error: str | None
    planning_route: str
    planning_activity_id: str
    ew_pending: dict
    ew_receipts: dict
    ew_sequences: dict


def public_event(state, event_type, data):
    return {'event_id': str(uuid4()), 'owner_run_id': state['agent_run_id'],
            'envelope': {'schema_version': 1, 'type': event_type, 'session_id': state['session_id'],
                         'run_id': state['public_run_id'], 'occurred_at': datetime.now(timezone.utc).isoformat(), 'data': data}}


def build_planning_graph(runtime, *, checkpointer):
    def receive(state):
        # Start each invocation with fresh plans/events; keep only bounded same-session conversation.
        current = {**state, 'public_run_id': state['run_id'], 'agent_run_id': state['run_id']}
        history = [*state.get('history', []), {'role': 'user', 'content': state['user_request']}][-runtime.settings.agent_history_message_limit:]
        events = [public_event(current, 'message.completed', {'role': 'user', 'channel': 'answer', 'content': [{'type': 'text', 'text': state['user_request']}]})]
        events.append(public_event(current, 'activity.started', {'activity_id': str(uuid4()), 'kind': 'planning', 'title': '요청에 맞는 답변 또는 분석 계획을 준비하고 있어요.'}))
        return {'agent_runtime': RUNTIME_VERSION, 'public_run_id': current['public_run_id'],
                'task_id': str(uuid4()),'kernel_profile':state.get('kernel_profile') or runtime.settings.executor_runtime_profile,
                'dataset_output_dir':f"/workspace/pv/user/{state['user_id']}/project/{state['project_id']}/data",
                'execution_id':None,'executor_operation_number':0,'next_step_sequence':0,
                'completed_steps':[],'skipped_steps':[],'execution_decisions':{},'pending_decisions':[],
                'decision_review':None,'execution_review_validation_error':None,'observations':[],'analysis_failure':False,'terminal_event_seen':False,
                'last_analysis_context':analysis_for_owner(state.get('last_analysis_context'),state,runtime.settings.agent_session_analysis_max_chars),
                'execution_snapshot':None,'failed_step_ids':[],'repair_attempts':0,'repair_max_attempts':0,
                'repair_authorized_level':0,'repair_candidate':None,'repair_review':None,'repair_history':[],
                'repair_action':'','repair_stop_reason':None,'repair_validation_error':None,
                'ew_pending':{},'ew_receipts':{},'ew_sequences':{},'execution_status':'','report_status':'',
                'agent_run_id': current['agent_run_id'], 'initial_request_receipt': state.get('initial_request_identity'),
                'user_resume_receipt': None, 'public_events': events, 'history': history,
                'planning_revision_count':0,'planning_feedback':[],'planning_previous_reviews':[],'planning_validation_error':None,'planning_question':None,'planning_route':'review',
                'reviews': [], 'plan_views': [], 'interaction_data': None, 'approved_snapshot': None,
                'final_response': None, 'review_action': None, 'review_error': None,
                'interaction_id': str(uuid4()), 'interaction_revision': 1, 'asset_revision': runtime.catalog.revision}

    async def converse(state):
        context = AgentContext(user_id=state['user_id'], project_id=state['project_id'], session_id=state['session_id'],
                               project_system_prompt=state.get('project_system_prompt', ''),
                               project_prompt_version=state.get('project_prompt_version'), model_selection=state['model_selection'],
                               session_analysis_context=state.get('last_analysis_context'))
        datasets = visible_datasets(runtime.datasets, state)
        reply = await runtime.respond(state, context, [{'dataset_id': key, 'title': item['title'], 'description': item.get('description', ''), 'scope': item['scope']}
                                                      for key, item in datasets.items()])
        reviews = []
        for proposal in reply.plans:
            definition = proposal.definition
            policy = {'allowed_modes': ['MULTI'] if definition['decisions'] or any('when' in s for s in definition['steps']) else ['SINGLE', 'MULTI'],
                      'repair_level_limit': runtime.settings.agent_repair_level_limit,
                      'max_repair_attempts_limit': runtime.settings.agent_max_repair_attempts,
                      'default_repair_level':runtime.settings.agent_repair_level,
                      'default_repair_attempts':runtime.settings.agent_max_repair_attempts}
            review = new_review(definition, proposal.input_values, runtime.catalog.metadata, policy)
            # Validate proposed data references with exactly the same rules as user edits.
            patch_review(review, {'action': 'edit_plan', 'plan_id': review['plan_id'], 'plan_revision': 1}, datasets=runtime.datasets, context=state)
            reviews.append(review)
        from ..execution.grounding import grounded_message
        # Explicit mock-provider responses intentionally have no LLM grounding contract.
        if runtime.models.resolve(state['model_selection']).provider == 'mock' and getattr(reply, 'grounding', None) is None:
            message = reply.message
        else:
            message = grounded_message(reply, context, max_chars=runtime.settings.agent_session_analysis_max_chars)
        channel = 'commentary' if reviews else 'answer'
        activity = next(e['envelope']['data']['activity_id'] for e in state['public_events'] if e['envelope']['type'] == 'activity.started')
        events = [*state['public_events'], public_event(state, 'activity.completed', {'activity_id': activity, 'kind': 'planning', 'title': '답변 또는 계획을 준비했습니다.'}),
                  public_event(state, 'message.completed', {'role': 'assistant', 'channel': channel, 'content': [{'type': 'text', 'text': message}]})]
        return {'reviews': reviews, 'routing_result': {'route': 'analysis' if reviews else 'faq'}, 'public_events': events,
                'history': [*state['history'], {'role': 'assistant', 'content': message}][-runtime.settings.agent_history_message_limit:],
                'final_response': None if reviews else {'status': 'answer', 'message': message}}

    def publish_review(state):
        owner = (state.get('user_resume_receipt') or {}).get('command_id', state['agent_run_id'])
        current = {**state, 'agent_run_id': owner}
        views = [plan_view(review) for review in state['reviews']]
        question = state.get('planning_question')
        data = {'interaction_id': state['interaction_id'], 'revision': state['interaction_revision'], 'kind': 'plan_review',
                'status': 'open', 'resume_token': owner, 'summary': '계획과 입력값을 확인하고 승인해 주세요.',
                'payload': {'plans': views, 'notices': [state['review_error']] if state.get('review_error') else []}}
        if question:
            data.update(kind='planning_question', summary='다른 계획을 준비하려면 추가 정보가 필요합니다.',
                        payload={'question':question,'notices':[state['review_error']] if state.get('review_error') else []})
        data['payload']['revision_policy']={'used':state.get('planning_revision_count',0),
            'limit':runtime.settings.agent_max_plan_revisions,'free_code_allowed':runtime.settings.agent_free_plan_enabled,
            'free_code_require_approval':runtime.settings.agent_free_plan_require_approval}
        event_type = 'interaction.opened' if state['interaction_revision'] == 1 else 'interaction.updated'
        return {'agent_run_id': owner, 'plan_views': views, 'interaction_data': data,
                'public_events': [*state['public_events'], public_event(current, event_type, data)]}

    @record_user_resume
    def await_review(state):
        command = user_interrupt(state['interaction_data'])
        action = command.get('resume') if isinstance(command, dict) else None
        return {'review_action': action, 'public_events': []}

    def apply_review(state):
        owner = (state.get('user_resume_receipt') or {}).get('command_id', state['agent_run_id'])
        current = {**state, 'agent_run_id': owner}
        action = state.get('review_action')
        try:
            if runtime.catalog.revision != state['asset_revision']:
                raise PlanReviewError('배포된 Skill·Tool이 변경되어 새 계획이 필요합니다.')
            if isinstance(action, dict) and action.get('action') in {'replan','answer_clarification'}:
                request = validate_plan_revision(state['interaction_data'], action,
                    count=state.get('planning_revision_count',0),limit=runtime.settings.agent_max_plan_revisions)
                feedback = {'action':request.action,'feedback':request.feedback,
                            'interaction_id':str(request.interaction_id),'revision':request.revision}
                activity_id = str(uuid4())
                return {'agent_run_id':owner,'planning_route':'revise',
                        'planning_revision_count':state.get('planning_revision_count',0)+1,
                        'planning_feedback':[*state.get('planning_feedback',[]),feedback],
                        'planning_activity_id':activity_id,'review_error':None,
                        'planning_previous_reviews':state['reviews'] or state.get('planning_previous_reviews',[]),
                        'history':[*state['history'],{'role':'user','content':request.feedback}][-runtime.settings.agent_history_message_limit:],
                        'interaction_revision':state['interaction_revision']+1,
                        'public_events':[public_event(current,'interaction.resolved',{
                            'interaction_id':state['interaction_id'],'revision':state['interaction_revision'],
                            'kind':state['interaction_data']['kind'],'status':'resolved','resolution':'replanning' if request.action=='replan' else 'answered',
                            'payload':{'notices':[]}}),
                            public_event(current,'message.completed',{'role':'user','channel':'answer','content':[{'type':'text','text':request.feedback}]}),
                            public_event(current,'activity.started',{'activity_id':activity_id,'kind':'planning','title':'피드백을 반영해 계획을 다시 준비하고 있어요.'})]}
            selected = next((r for r in state['reviews'] if r['plan_id'] == (action or {}).get('plan_id')), None)
            if selected is None:
                raise PlanReviewError('Unknown plan')
            updated = patch_review(selected, action, datasets=runtime.datasets, context=state)
            reviews = [updated if r['plan_id'] == selected['plan_id'] else r for r in state['reviews']]
            result = {'reviews': reviews, 'agent_run_id': owner, 'interaction_revision': state['interaction_revision'] + 1, 'review_error': None,'planning_route':'review'}
            if updated['consumed']:
                snapshot = freeze_approval(updated, runtime.catalog.sources, runtime.catalog.skill_sources, state, runtime.catalog.revision, runtime.datasets)
                view = plan_view(updated)
                data = {'interaction_id': state['interaction_id'], 'revision': state['interaction_revision'], 'kind': 'plan_review',
                        'status': 'resolved', 'resolution': 'approved', 'payload': {'approved_plan': view, 'notices': []}}
                result.update({'approved_snapshot': snapshot, 'interaction_data': None,
                               'final_response': {'status': 'plan_approved', 'approved_plan': view},
                               'public_events': [public_event(current, 'interaction.resolved', data)]})
            return result
        except (ValueError, TypeError) as exc:
            # A consumed command always checkpoints its receipt. A bad form opens a new wait, never a blind retry.
            return {'agent_run_id': owner, 'review_error': str(exc), 'interaction_revision': state['interaction_revision'] + 1,'planning_route':'review'}

    async def revise(state):
        context = AgentContext(user_id=state['user_id'],project_id=state['project_id'],session_id=state['session_id'],
            project_system_prompt=state.get('project_system_prompt',''),project_prompt_version=state.get('project_prompt_version'),
            model_selection=state['model_selection'], session_analysis_context=state.get('last_analysis_context'))
        datasets = visible_datasets(runtime.datasets,state)
        visible = [{'dataset_id':key,'title':item['title'],'description':item.get('description',''),'scope':item['scope']} for key,item in datasets.items()]
        try:
            reply = await runtime.revise(state,context,visible)
            reviews = validate_revision_reply(reply,runtime,state)
        except (StructuredResponseError, ValueError, SyntaxError, KeyError, TypeError) as exc:
            # Invalid model content consumes this user revision, never authorizes execution.
            return {'planning_route':'review','planning_validation_error':str(exc)[:4000],'review_error':'계획 응답을 검증하지 못했습니다. 기존 계획을 확인하거나 다시 요청해 주세요.',
                'public_events':[*state['public_events'],public_event(state,'activity.completed',{'activity_id':state['planning_activity_id'],'kind':'planning','title':'새 계획을 검증하지 못했습니다.'})]}
        events = [*state['public_events'],public_event(state,'activity.completed',{'activity_id':state['planning_activity_id'],'kind':'planning','title':'피드백을 반영했습니다.'}),
            public_event(state,'message.completed',{'role':'assistant','channel':'commentary','content':[{'type':'text','text':reply.message}]})]
        result = {'planning_route':'review','reviews':reviews,'planning_question':reply.message if not reviews else None,
            'history':[*state['history'],{'role':'assistant','content':reply.message}][-runtime.settings.agent_history_message_limit:],
            'public_events':events,'review_error':None,'planning_validation_error':None,'interaction_data':None,'approved_snapshot':None,'final_response':None}
        # Configuration permits only an unambiguous complete free-code proposal.
        if len(reviews)==1 and reviews[0]['execution_kind']=='free_code' and not runtime.settings.agent_free_plan_require_approval:
            try:
                approved = patch_review(reviews[0],{'action':'approve_plan','plan_id':reviews[0]['plan_id'],'plan_revision':1},datasets=runtime.datasets,context=state)
            except PlanReviewError:
                return result  # Missing required inputs remain an editable HITL form.
            approved['approval_mode']='configuration'
            approved['user_actions'].append({'action':'configuration_approval','setting':'AGENT_FREE_PLAN_REQUIRE_APPROVAL','value':False,
                'feedback_revision':state['interaction_revision']})
            snapshot = freeze_approval(approved,runtime.catalog.sources,runtime.catalog.skill_sources,state,runtime.catalog.revision,runtime.datasets)
            view=plan_view(approved)
            result.update(reviews=[approved],approved_snapshot=snapshot,
                final_response={'status':'plan_approved','approved_plan':view},
                public_events=[*events,public_event(state,'interaction.resolved',{'interaction_id':state['interaction_id'],
                    'revision':state['interaction_revision'],'kind':'plan_review','status':'resolved','resolution':'auto_approved',
                    'payload':{'approved_plan':view,'notices':['설정에 따라 실행별 코드 계획을 자동 승인하고 실행합니다.']}})])
        return result

    def review_route(state):
        if state.get('planning_route')=='revise':return 'revise_plan'
        if state.get('approved_snapshot'):return 'execution_select' if runtime.execution_enabled else END
        return 'publish_review'

    builder = StateGraph(PlanningState)
    builder.add_node('receive', receive)
    builder.add_node('conversation', converse)
    builder.add_node('publish_review', publish_review)
    builder.add_node('await_review', await_review)
    builder.add_node('apply_review', apply_review)
    builder.add_node('revise_plan',revise)
    builder.add_edge(START, 'receive')
    builder.add_edge('receive', 'conversation')
    builder.add_conditional_edges('conversation', lambda s: 'publish_review' if s['reviews'] else END)
    builder.add_edge('publish_review', 'await_review')
    builder.add_edge('await_review', 'apply_review')
    if runtime.execution_enabled:
        from agent_service.agents.analysis.execution.nodes import wire_execution
        wire_execution(builder,runtime,public_event)
    builder.add_conditional_edges('apply_review',review_route)
    builder.add_conditional_edges('revise_plan',review_route)
    graph = builder.compile(checkpointer=checkpointer, name=RUNTIME_VERSION)
    return graph

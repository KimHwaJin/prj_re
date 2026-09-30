"""Per-process immutable assets and lazy, pinned model/Agent instances."""
from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
from agent_service.agents.analysis.dependencies import create_chat_model
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from service_runtime.model_selection import build_catalog


class PlanningRuntime:
    def __init__(self, settings, catalog=None, *, executor=None, bindings=None):
        self.settings = settings
        self.catalog = catalog or AssetCatalog()
        self.models = settings.model_catalog or build_catalog(settings)
        self.agents = {}
        self.datasets = settings.analysis_datasets
        self.executor = executor
        self.bindings = bindings
        self.execution_agents = {}

    @property
    def execution_enabled(self):
        return self.settings.executor_submit_enabled and self.executor is not None

    async def execution_role(self, role, state, payload):
        from agent_service.context import AgentContext
        spec = self.models.resolve(state['model_selection'])
        context = AgentContext(user_id=state['user_id'],project_id=state['project_id'],session_id=state['session_id'],
            project_system_prompt=state.get('project_system_prompt',''),project_prompt_version=state.get('project_prompt_version'),
            model_selection=state['model_selection'])
        if spec.provider == 'mock':
            from agent_service.agents.analysis.planning.testing import mock_execution_role
            return mock_execution_role(role,payload)
        discovery=role=='repair' and payload['repair_context']['repair_authorized_level']>=3
        key = (role,state['model_selection']['name'],state['model_selection']['revision'],discovery)
        if key not in self.execution_agents:
            if role == 'review':
                from agent_service.agents.analysis.agent_builders.execution_review.agent import build_agent
            elif role == 'report':
                from agent_service.agents.analysis.agent_builders.execution_report.agent import build_agent
            if role=='repair':
                import json
                from langchain_core.messages import HumanMessage
                from agent_service.agents.analysis.agent_builders.execution_repair.agent import build_agent
                from agent_service.agents.analysis.execution.repair_policy import proposal_snapshot
                def validate_repair(response,request):
                    payload=json.loads(next(m.content for m in request.messages if isinstance(m,HumanMessage)))
                    if response.can_repair:
                        try:
                            proposal_snapshot(payload['repair_context'],response,self.catalog,level_limit=self.settings.agent_repair_level_limit)
                        except (SyntaxError,KeyError,TypeError) as exc:
                            raise ValueError('Invalid repair structure/source: '+str(exc)) from exc
                    elif response.argument_changes or response.source_changes or response.replacement_steps or response.replacement_decisions:
                        raise ValueError('can_repair=false must not contain execution changes')
                self.execution_agents[key]=build_agent(create_chat_model(spec.apply(self.settings)),self.catalog,
                    discovery_max_rounds=self.settings.agent_discovery_max_rounds,enable_discovery=discovery,
                    structured_output_mode=spec.structured_output_mode,validate_response=validate_repair)
            else:
                self.execution_agents[key] = build_agent(create_chat_model(spec.apply(self.settings)),structured_output_mode=spec.structured_output_mode)
        return await self.execution_agents[key].ainvoke(payload,context=context)

    async def respond(self, state, context, dataset_catalog):
        selected = state['model_selection']
        spec = self.models.resolve(selected)
        key = (selected['name'], selected['revision'])
        if key not in self.agents:
            if spec.provider == 'mock':
                from agent_service.agents.analysis.planning.testing import MockConversation
                self.agents[key] = MockConversation(self.catalog, spec.mock_delay_ms)
            else:
                self.agents[key] = build_agent(create_chat_model(spec.apply(self.settings)), self.catalog,
                                               max_candidates=self.settings.max_plan_candidates,
                                               discovery_max_rounds=self.settings.agent_discovery_max_rounds,
                                               repair_limit=self.settings.agent_repair_level_limit,repair_attempts=self.settings.agent_max_repair_attempts,
                                               structured_output_mode=spec.structured_output_mode)
        return await self.agents[key].ainvoke({
            'request': state['user_request'], 'history': state.get('history', [])[-self.settings.agent_history_message_limit:],
            'available_skills': self.catalog.public_skills(), 'dataset_catalog': dataset_catalog,
            'max_candidates': self.settings.max_plan_candidates,
            'execution_policy': {'repair_level_limit':self.settings.agent_repair_level_limit,
                'max_repair_attempts_limit':self.settings.agent_max_repair_attempts,
                'default_repair_level':self.settings.agent_repair_level,'default_repair_attempts':self.settings.agent_max_repair_attempts},
        }, context=context)

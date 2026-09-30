"""Per-process immutable assets and lazy, pinned model/Agent instances."""
from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
from agent_service.agents.analysis.dependencies import create_chat_model
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from service_runtime.model_selection import build_catalog


class PlanningRuntime:
    def __init__(self, settings, catalog=None):
        self.settings = settings
        self.catalog = catalog or AssetCatalog()
        self.models = settings.model_catalog or build_catalog(settings)
        self.agents = {}
        self.datasets = settings.analysis_datasets

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
                                               structured_output_mode=spec.structured_output_mode)
        return await self.agents[key].ainvoke({
            'request': state['user_request'], 'history': state.get('history', [])[-self.settings.agent_history_message_limit:],
            'available_skills': self.catalog.public_skills(), 'dataset_catalog': dataset_catalog,
            'max_candidates': self.settings.max_plan_candidates,
        }, context=context)

"""Process-scoped HTTP pool; reuse the existing CRUD DB pool (no extra DB)."""

from .embedding import OpenAICompatibleEmbedding
from .indexing import WorkflowIndexer
from .retrieval import WorkflowSearch

_runtime = None


class WorkflowRuntime:
    def __init__(self, settings, session_factory):
        self.embedding = OpenAICompatibleEmbedding(settings.workflow_search)
        self.indexer = WorkflowIndexer(
            settings.workflow_search, self.embedding, session_factory
        )
        self.search = WorkflowSearch(
            settings.workflow_search,
            self.embedding,
            session_factory,
            enabled=settings.agent.workflow_recommendation_enabled,
            similarity_threshold=settings.agent.workflow_similarity_score,
        )

    async def close(self):
        await self.embedding.close()


def get_workflow_runtime():
    global _runtime
    if _runtime is None:
        from service_settings import get_settings
        from api_service.infrastructure.database import get_session_factory

        _runtime = WorkflowRuntime(get_settings(), get_session_factory())
    return _runtime


async def close_workflow_runtime():
    global _runtime
    runtime, _runtime = _runtime, None
    if runtime is not None:
        await runtime.close()

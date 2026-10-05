"""Embedding I/O budgets and honest configuration/unavailable states."""

import httpx
import pytest
from api_service.workflows.embedding import (
    OpenAICompatibleEmbedding,
    EmbeddingUnavailable,
    validate_vectors,
)
from api_service.workflows.retrieval import WorkflowSearch
from service_runtime.workflow_search_settings import WorkflowSearchSettings
from service_settings import load_settings, ConfigurationError


@pytest.mark.asyncio
async def test_batches_reorder_vectors_and_reject_bad_dimensions():
    settings = WorkflowSearchSettings(
        base_url="http://embed.invalid/v1",
        model="test",
        dimensions=2,
        embedding_batch_size=2,
    )
    provider = OpenAICompatibleEmbedding(settings)
    calls = []

    def handle(request):
        import json

        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [i + 1, 1]}
                    for i in reversed(range(len(body["input"])))
                ]
            },
        )

    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        assert await provider.embed(["one", "two", "three"]) == [[1, 1], [2, 1], [1, 1]]
        assert [len(b["input"]) for b in calls] == [2, 1]
        assert all(
            "dimensions" not in body for body in calls
        )  # custom providers infer their native model dimension
    finally:
        await provider.close()
    for value in [[[0, 0]], [[1]], [[float("nan"), 1]], [[True, 1]]]:
        with pytest.raises(EmbeddingUnavailable):
            validate_vectors(value, 1, 2)


@pytest.mark.asyncio
async def test_embedding_deadline_includes_semaphore_admission():
    settings = WorkflowSearchSettings(
        base_url="http://embed.invalid/v1",
        model="test",
        dimensions=2,
        embedding_timeout_seconds=0.01,
    )
    provider = OpenAICompatibleEmbedding(settings)
    await provider._semaphore.acquire()
    await provider._semaphore.acquire()
    with pytest.raises(EmbeddingUnavailable, match="transport_failed"):
        await provider.embed(["request"])
    assert provider._client is None


@pytest.mark.asyncio
async def test_disabled_and_unconfigured_search_open_no_db_or_model():
    class Forbidden:
        async def embed(self, texts):
            raise AssertionError("model was called")

    def factory():
        raise AssertionError("DB was opened")

    for enabled, reason in [(False, "disabled"), (True, "unconfigured")]:
        result = await WorkflowSearch(
            WorkflowSearchSettings(), Forbidden(), factory, enabled=enabled
        ).search("test")
        assert result.diagnostics.termination == reason and not result.items


def test_central_configuration_and_model_space():
    with pytest.raises(ConfigurationError, match="Workflow search"):
        load_settings(config={"WORKFLOW_EMBEDDING_MODEL": "test"}, environ={})
    first = load_settings(
        config={
            "WORKFLOW_EMBEDDING_BASE_URL": "http://embed.invalid/v1",
            "WORKFLOW_EMBEDDING_MODEL": "test",
            "WORKFLOW_EMBEDDING_DIMENSIONS": 3,
            "WORKFLOW_SEARCH_MAX_ROUNDS": 2,
        },
        environ={},
    )
    assert first.workflow_search.max_rounds == 2
    assert (
        first.workflow_search.space
        != first.workflow_search.model_copy(update={"model_revision": "next"}).space
    )
    assert "CREATE INDEX" in first.workflow_search.index_sql()

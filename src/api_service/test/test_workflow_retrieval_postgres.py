"""Isolated real pgvector: grouping, publication races, API and query plans."""

import asyncio
from dataclasses import replace
from uuid import UUID, uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from api_service.test.test_planning_api_postgres import test_config, planning  # noqa: F401
from api_service.test.test_user_identity_postgres import headers, add_user
from agent_service.agents.analysis.tests.asset_fixtures import assets
from agent_service.agents.analysis.tests.test_workflow_standard import public_document
from api_service.models.common.workflow_model import (
    WorkflowModel,
    WorkflowEmbeddingModel,
)
from api_service.services.workflow_service import WorkflowService
from api_service.services.workflow_file_store import WorkflowFileStore
from api_service.schemas.common.workflow_schema import WorkflowUpdate
from api_service.workflows.runtime import WorkflowRuntime
from api_service.workflows.embedding import EmbeddingUnavailable
from service_runtime.workflow_search_settings import WorkflowSearchSettings
import service_settings


class Embeddings:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise EmbeddingUnavailable("embedding_transport_failed")
        return [
            [
                1.0,
                0.01
                if t.startswith("alpha")
                else 0.2
                if t.startswith("beta")
                else 0.4
                if t.startswith("gamma")
                else 0.0,
                0.0,
            ]
            for t in texts
        ]


@pytest_asyncio.fixture
async def search_context(planning, tmp_path, monkeypatch):  # noqa: F811
    h = planning
    catalog, case = assets(tmp_path / "assets", "inventory")
    import api_service.services.workflow_service as service
    import api_service.api.v1.routes.workflows as routes

    monkeypatch.setattr(service, "deployed_analysis_assets", lambda: catalog)
    monkeypatch.setattr(
        WorkflowFileStore, "_root", staticmethod(lambda: tmp_path / "workflows")
    )
    policy = WorkflowSearchSettings(
        base_url="http://embedding.invalid/v1",
        model="test",
        dimensions=3,
        candidate_limit=3,
        batch_size=2,
        max_rounds=8,
        ef_search=1000,
        timeout_ms=3000,
    )
    settings = replace(service_settings.get_settings(), workflow_search=policy)
    runtime = WorkflowRuntime(settings, h.factory)
    embedding = Embeddings()
    runtime.embedding = embedding
    runtime.indexer.embedding = embedding
    runtime.search.embedding = embedding
    runtime.search.similarity_threshold = 0
    monkeypatch.setattr(routes, "get_workflow_runtime", lambda: runtime)
    async with h.factory() as db:
        await db.execute(text(policy.index_sql()))
        await db.commit()
    h.search_runtime = runtime
    h.policy = policy
    h.embedding = embedding
    h.doc = public_document(case)
    return h


async def candidate(h, queries):
    response = await h.client.post(
        "/api/v1/workflows",
        headers=headers(h.user["user_id"]),
        json={"user_queries": queries, "document": h.doc},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def promote(h, queries):
    row = await candidate(h, queries)
    response = await h.client.post(
        "/api/v1/workflows/" + row["workflow_id"] + "/promote",
        headers=headers(h.user["user_id"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("aliases", [50, 500])
async def test_distinct_workflows_repeat_exclusion_and_native_hnsw(
    search_context, aliases
):
    h = search_context
    a = await promote(h, ["alpha " + str(i) for i in range(aliases)])
    b = await promote(h, ["beta " + str(i) for i in range(aliases)])
    c = await promote(h, ["gamma"])
    await candidate(h, ["alpha private"])
    assert (
        len(h.embedding.calls) == 4
    )  # three registrations + private candidate; promotion reuses vectors

    result = (
        await h.client.post(
            "/api/v1/workflows/search",
            headers=headers(h.user["user_id"]),
            json={"query": "search"},
        )
    ).json()
    expected = [a["workflow_id"], b["workflow_id"], c["workflow_id"]]
    returned = [i["workflow_id"] for i in result["items"]]
    assert len(returned) == len(set(returned)) and set(returned) <= set(expected)
    assert returned and returned[0] == a["workflow_id"]
    assert result["diagnostics"]["reranked"] and result["diagnostics"]["approximate"]
    assert result["diagnostics"]["rounds"] <= h.policy.max_rounds
    if result["diagnostics"]["termination"] == "candidate_limit":
        assert returned == expected
    else:
        # HNSW has no group-completeness guarantee, especially for many exact
        # duplicate vectors. An ANN exhausted result must remain approximate.
        assert result["diagnostics"]["termination"] in {
            "no_more_ann_candidates",
            "round_limit",
            "timeout",
        }
    import json, os

    report = os.environ.get("DTEST_WORKFLOW_RETRIEVAL_REPORT")
    if report:
        from pathlib import Path

        with Path(report).open("a") as output:
            output.write(
                json.dumps(
                    {
                        "aliases_per_major_workflow": aliases,
                        "expected_distinct": 3,
                        "returned_distinct": len(returned),
                        "diagnostics": result["diagnostics"],
                    }
                )
                + "\n"
            )
    async with h.factory() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM workflow_embeddings WHERE is_active")
            )
            == 2 * aliases + 1
        )
        await db.execute(text("SET LOCAL enable_seqscan=off"))
        plan = await db.scalar(
            text(
                f"EXPLAIN (FORMAT JSON) SELECT workflow_id FROM workflow_embeddings WHERE {h.policy.predicate} ORDER BY vector_values::vector(3) <=> '[1,0,0]'::vector(3) LIMIT 2"
            )
        )
        assert h.policy.index_name in str(plan)
        assert "Index Scan" in str(plan)


@pytest.mark.asyncio
async def test_query_only_edit_cas_and_delete_deactivate(search_context):
    h = search_context
    row = await promote(h, ["alpha"])
    path = "/api/v1/workflows/" + row["workflow_id"]
    responses = await asyncio.gather(
        *(
            h.client.patch(
                path,
                headers=headers(h.user["user_id"]),
                json={
                    "user_queries": [q],
                    "expected_resource_revision": row["resource_revision"],
                },
            )
            for q in ["beta", "gamma"]
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    current = (await h.client.get(path, headers=headers(h.user["user_id"]))).json()
    assert (
        current["content_sha256"] == row["content_sha256"]
        and current["search_revision"] == 2
    )
    assert current["resource_revision"] == 2 and current["index_state"] == "ready"
    async with h.factory() as db:
        active = (
            await db.scalars(
                select(WorkflowEmbeddingModel).where(WorkflowEmbeddingModel.is_active)
            )
        ).all()
        assert len(active) == 1 and active[0].search_revision == 2
    assert (
        await h.client.delete(path, headers=headers(h.user["user_id"]))
    ).status_code == 204
    result = await h.search_runtime.search.search("search")
    assert (
        not result.items and result.diagnostics.termination == "no_more_ann_candidates"
    )


@pytest.mark.asyncio
async def test_embedding_failure_preserves_registration_and_retry(search_context):
    h = search_context
    h.embedding.fail = True
    row = await promote(h, ["alpha"])
    assert (
        row["index_state"] == "failed"
        and row["index_error"] == "embedding_transport_failed"
    )
    assert WorkflowFileStore.read(row["file_path"]) == h.doc
    h.embedding.fail = False
    response = await h.client.post(
        "/api/v1/workflows/" + row["workflow_id"] + "/reindex",
        headers=headers(h.user["user_id"]),
    )
    assert response.status_code == 200 and response.json()["index_state"] == "ready"
    result = await h.search_runtime.search.search("search")
    assert len(result.items) == 1
    other = await add_user(h, name="index-owner")
    assert (
        await h.client.post(
            "/api/v1/workflows/" + row["workflow_id"] + "/reindex",
            headers=headers(other["user_id"]),
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_delayed_publish_cannot_restore_old_query_and_releases_db(search_context):
    h = search_context
    row = await promote(h, ["alpha"])
    engine = create_async_engine(
        h.factory.kw["bind"].url, pool_size=1, max_overflow=0, pool_timeout=0.3
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    started = asyncio.Event()
    release = asyncio.Event()
    original = h.embedding.embed

    async def slow(texts):
        started.set()
        await release.wait()
        return await original(texts)

    h.embedding.embed = slow
    h.search_runtime.indexer.session_factory = factory
    # A genuinely new query needs network work, unlike a promotion/retry whose
    # identical query can be resolved from the source candidate's vector cache.
    async with factory() as db:
        await WorkflowService.update(
            db,
            UUID(row["created_by_user_id"]),
            UUID(row["workflow_id"]),
            WorkflowUpdate(user_queries=["beta"], expected_resource_revision=1),
        )
    task = asyncio.create_task(
        h.search_runtime.indexer.index(UUID(row["workflow_id"]), 2)
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        # Same one-connection pool can edit while embedding waits.
        async with factory() as db:
            await WorkflowService.update(
                db,
                UUID(row["created_by_user_id"]),
                UUID(row["workflow_id"]),
                WorkflowUpdate(user_queries=["gamma"], expected_resource_revision=2),
            )
        release.set()
        assert await asyncio.wait_for(task, 3) == "superseded"
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM workflow_embeddings WHERE is_active")
                )
                == 0
            )
    finally:
        release.set()
        await task
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_index_no_hidden_exact_fallback_and_bounded_rounds(
    search_context,
):
    h = search_context
    await promote(h, ["alpha"] * 1)
    h.search_runtime.search.settings = h.policy.model_copy(
        update={"max_rounds": 1, "candidate_limit": 20}
    )
    result = await h.search_runtime.search.search("search")
    assert len(result.items) == 1 and result.diagnostics.termination == "round_limit"
    async with h.factory() as db:
        await db.execute(text("DROP INDEX " + h.policy.index_name))
        await db.commit()
    result = await h.search_runtime.search.search("search")
    assert not result.items and result.diagnostics.termination == "index_unavailable"


@pytest.mark.asyncio
async def test_query_validation_and_conflict_token(search_context):
    h = search_context
    for query in [[], [" "], ["same", " same "], ["x" * 4001]]:
        response = await h.client.post(
            "/api/v1/workflows",
            headers=headers(h.user["user_id"]),
            json={"document": h.doc, "user_queries": query},
        )
        assert response.status_code == 422
    row = await candidate(h, ["valid"])
    path = "/api/v1/workflows/" + row["workflow_id"]
    response = await h.client.patch(
        path,
        headers=headers(h.user["user_id"]),
        json={
            "user_queries": ["new"],
            "expected_content_sha256": row["content_sha256"],
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_failed_concurrent_reindex_preserves_success(search_context):
    h = search_context
    row = await promote(h, ["alpha"])
    # No source cache for this attempt, so the injected transport failure is real.
    async with h.factory() as db:
        current = await db.get(WorkflowModel, UUID(row["workflow_id"]))
        current.source_workflow_id = None
        await db.commit()
    h.embedding.fail = True
    assert await h.search_runtime.indexer.index(UUID(row["workflow_id"]), 1) == "ready"
    async with h.factory() as db:
        current = await db.get(WorkflowModel, UUID(row["workflow_id"]))
        assert current.index_state == "ready"
        assert (
            await db.scalar(
                text("SELECT count(*) FROM workflow_embeddings WHERE is_active")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_search_budget_includes_pool_admission(search_context):
    h = search_context
    runtime = h.search_runtime.search
    runtime.settings = h.policy.model_copy(update={"timeout_ms": 10})

    class DelayedSession:
        async def __aenter__(self):
            await asyncio.sleep(0.1)
            raise AssertionError("Timeout must fire before any query")

        async def __aexit__(self, *args):
            pass

    runtime.session_factory = DelayedSession
    result = await runtime.search("search")
    assert not result.items and result.diagnostics.termination == "timeout"


@pytest.mark.asyncio
async def test_model_space_switch_preserves_prior_vectors(search_context):
    h = search_context
    row = await promote(h, ["alpha"])
    previous = h.policy.space
    changed = h.policy.model_copy(
        update={"base_url": "http://second-" + uuid4().hex + ".invalid/v1"}
    )
    h.search_runtime.indexer.settings = changed
    assert await h.search_runtime.indexer.index(UUID(row["workflow_id"]), 1) == "ready"
    async with h.factory() as db:
        rows = (
            await db.scalars(
                select(WorkflowEmbeddingModel).where(
                    WorkflowEmbeddingModel.workflow_id == UUID(row["workflow_id"])
                )
            )
        ).all()
        assert len(rows) == 2 and sum(e.is_active for e in rows) == 1
        assert next(e for e in rows if e.model_space == previous).status == "superseded"
    h.search_runtime.search.settings = changed
    result = await h.search_runtime.search.search("search")
    assert result.diagnostics.termination == "index_unavailable"
    async with h.factory() as db:
        await db.execute(text(changed.index_sql()))
        await db.commit()
    result = await h.search_runtime.search.search("search")
    assert len(result.items) == 1

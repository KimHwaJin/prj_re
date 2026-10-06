"""Revision-safe embedding publication using only short CRUD sessions."""

from hashlib import sha256
from sqlalchemy import select, update, or_
from sqlalchemy.dialects.postgresql import insert
from dtest.infrastructure.database.models.workflow_model import (
    WorkflowModel,
    WorkflowEmbeddingModel,
)
from dtest.contracts.values import utc_now
from .embedding import EmbeddingUnavailable, validate_vectors


class WorkflowIndexer:
    def __init__(self, settings, embedding, session_factory):
        self.settings, self.embedding, self.session_factory = (
            settings,
            embedding,
            session_factory,
        )

    async def index(self, workflow_id, expected_revision):
        # Snapshot and release DB before waiting for embedding admission/network.
        async with self.session_factory() as db:
            row = await db.scalar(
                select(WorkflowModel).where(WorkflowModel.workflow_id == workflow_id)
            )
            if (
                row is None
                or row.deleted_at is not None
                or row.search_revision != expected_revision
            ):
                return "superseded"
            queries = list(row.user_queries)
            cached = {}
            if self.settings.configured:
                origins = [
                    WorkflowEmbeddingModel.workflow_id == workflow_id,
                    WorkflowEmbeddingModel.search_revision < expected_revision,
                ]
                sources = origins[0] & origins[1]
                if row.source_workflow_id is not None:
                    sources = or_(
                        sources,
                        WorkflowEmbeddingModel.workflow_id == row.source_workflow_id,
                    )
                embeddings = (
                    await db.scalars(
                        select(WorkflowEmbeddingModel)
                        .where(
                            sources,
                            WorkflowEmbeddingModel.model_space == self.settings.space,
                            WorkflowEmbeddingModel.vector_values.is_not(None),
                            WorkflowEmbeddingModel.embedded_text.in_(queries),
                        )
                        .distinct(WorkflowEmbeddingModel.embedded_text)
                        .order_by(
                            WorkflowEmbeddingModel.embedded_text,
                            WorkflowEmbeddingModel.search_revision.desc(),
                            WorkflowEmbeddingModel.embedded_at.desc(),
                            WorkflowEmbeddingModel.embedding_id,
                        )
                    )
                ).all()
                cached = {
                    item.embedded_text: [float(value) for value in item.vector_values]
                    for item in embeddings
                }

        reason = None
        vectors = []
        if not self.settings.configured:
            reason = "embedding_unconfigured"
        elif not queries:
            reason = "user_queries_required"
        else:
            try:
                vectors = validate_vectors(
                    [cached[q] for q in queries]
                    if all(q in cached for q in queries)
                    else await self.embedding.embed(queries),
                    len(queries),
                    self.settings.dimensions,
                )
            except EmbeddingUnavailable as exc:
                reason = str(exc)
        # One resource lock serializes publish vs edits/deletes. All aliases
        # switch atomically; delayed completion cannot publish an older revision.
        async with self.session_factory() as db:
            row = await db.scalar(
                select(WorkflowModel)
                .where(WorkflowModel.workflow_id == workflow_id)
                .with_for_update()
            )
            if (
                row is None
                or row.deleted_at is not None
                or row.search_revision != expected_revision
            ):
                return "superseded"
            if reason and row.index_state == "ready" and self.settings.configured:
                ready = await db.scalar(
                    select(WorkflowEmbeddingModel.embedding_id)
                    .where(
                        WorkflowEmbeddingModel.workflow_id == workflow_id,
                        WorkflowEmbeddingModel.search_revision == expected_revision,
                        WorkflowEmbeddingModel.model_space == self.settings.space,
                        WorkflowEmbeddingModel.is_active.is_(True),
                        WorkflowEmbeddingModel.status == "ready",
                    )
                    .limit(1)
                )
                if ready is not None:
                    return "ready"  # A successful same-space retry wins.
            await db.execute(
                update(WorkflowEmbeddingModel)
                .where(
                    WorkflowEmbeddingModel.workflow_id == workflow_id,
                    WorkflowEmbeddingModel.status != "superseded",
                )
                .values(is_active=False, status="superseded")
            )
            if reason:
                row.index_state = "failed"
                row.index_error = reason
            else:
                batch = []
                for query, vector in zip(queries, vectors, strict=True):
                    values = dict(
                        workflow_id=workflow_id,
                        search_revision=expected_revision,
                        embedded_text=query,
                        embedded_text_sha256=sha256(query.encode()).hexdigest(),
                        search_metadata={},
                        model_provider="openai_compatible",
                        model_name=self.settings.model,
                        model_revision=self.settings.model_revision,
                        model_space=self.settings.space,
                        dimensions=self.settings.dimensions,
                        vector_values=vector,
                        status="ready",
                        is_active=row.lifecycle == "template" and row.is_recommendable,
                        failure_reason=None,
                        embedded_at=utc_now(),
                    )
                    batch.append(values)
                    if len(batch) >= self.settings.embedding_batch_size:
                        await self._publish_batch(db, batch)
                        batch = []
                if batch:
                    await self._publish_batch(db, batch)
                row.index_state = "ready"
                row.index_error = None
            await db.commit()
        return "failed" if reason else "ready"

    @staticmethod
    async def _publish_batch(db, values):
        statement = insert(WorkflowEmbeddingModel).values(values)
        immutable = {
            "workflow_id",
            "search_revision",
            "model_space",
            "embedded_text_sha256",
        }
        changes = {
            key: getattr(statement.excluded, key)
            for key in values[0]
            if key not in immutable
        }
        changes["updated_at"] = utc_now()
        await db.execute(
            statement.on_conflict_do_update(
                constraint="uq_workflow_embeddings_source_model", set_=changes
            )
        )

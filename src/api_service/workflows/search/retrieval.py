"""Bounded distinct-Workflow HNSW candidates, exact scoring only inside them."""

import asyncio
from time import monotonic
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from service_contracts.workflow_retrieval import (
    WorkflowSearchResult,
    WorkflowSearchDiagnostics,
    WorkflowSearchCandidate,
)
from api_service.workflows.file_store import WorkflowFileStore
from .embedding import EmbeddingUnavailable, validate_vectors


class WorkflowSearch:
    def __init__(
        self,
        settings,
        embedding,
        session_factory,
        *,
        enabled=True,
        similarity_threshold=0.0,
    ):
        self.settings, self.embedding, self.session_factory = (
            settings,
            embedding,
            session_factory,
        )
        self.enabled, self.similarity_threshold = enabled, similarity_threshold

    async def search(self, query):
        start = monotonic()
        rounds = ann_rows = 0
        found = []
        rows = []
        reason = "round_limit"
        reranked = False

        def result(items=()):
            return WorkflowSearchResult(
                items=list(items),
                diagnostics=WorkflowSearchDiagnostics(
                    termination=reason,
                    rounds=rounds,
                    ann_rows=ann_rows,
                    distinct_candidates=len(found),
                    elapsed_ms=round((monotonic() - start) * 1000, 2),
                    reranked=reranked,
                ),
            )

        if not self.enabled:
            reason = "disabled"
            return result()
        if not self.settings.configured:
            reason = "unconfigured"
            return result()
        # Model service wait must never hold a database connection.
        try:
            vector = validate_vectors(
                await self.embedding.embed([query]), 1, self.settings.dimensions
            )[0]
        except EmbeddingUnavailable:
            reason = "embedding_unavailable"
            return result()
        s = self.settings
        vector_text = "[" + ",".join(str(x) for x in vector) + "]"
        parameters = {"vector": vector_text, "excluded": [], "limit": s.batch_size}
        distance = f"(vector_values::vector({s.dimensions})) <=> CAST(:vector AS vector({s.dimensions}))"
        # Partial-index predicates are trusted settings literals so PostgreSQL's
        # prepared/generic plans can recognize the exact model-space index.
        ann = text(
            f"SELECT workflow_id, {distance} AS distance FROM workflow_embeddings WHERE {s.predicate} AND NOT (workflow_id = ANY(CAST(:excluded AS uuid[]))) ORDER BY {distance} LIMIT :limit"
        )
        gather_deadline = monotonic() + s.timeout_ms / 1000 * 0.75
        try:
            async with asyncio.timeout(s.timeout_ms / 1000):
                async with self.session_factory() as db:
                    # Search candidates, scores and pinned paths share one DB snapshot.
                    await db.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    )
                    valid = await db.scalar(
                        text(
                            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname=:name"
                        ),
                        {"name": s.index_name},
                    )
                    if not valid:
                        reason = "index_unavailable"
                        return result()
                    # ANN is a deliberate production choice even on a small
                    # catalog; do not let a sequential exact search replace it.
                    previous_seqscan = await db.scalar(
                        text("SELECT current_setting('enable_seqscan')")
                    )
                    await db.execute(
                        text("SELECT set_config('enable_seqscan','off',true)")
                    )
                    for name, value in {
                        "hnsw.ef_search": s.ef_search,
                        "hnsw.max_scan_tuples": s.max_scan_tuples,
                        "hnsw.scan_mem_multiplier": s.scan_mem_multiplier,
                        "hnsw.iterative_scan": "relaxed_order",
                    }.items():
                        await db.execute(
                            text("SELECT set_config(:name,:value,true)"),
                            {"name": name, "value": str(value)},
                        )
                    for _ in range(s.max_rounds):
                        if monotonic() >= gather_deadline:
                            reason = "timeout"
                            break
                        rounds += 1
                        parameters["excluded"] = found
                        batch = (await db.execute(ann, parameters)).mappings().all()
                        ann_rows += len(batch)
                        for row in batch:
                            if row["workflow_id"] not in found:
                                found.append(row["workflow_id"])
                                if len(found) >= s.candidate_limit:
                                    break
                        if len(found) >= s.candidate_limit:
                            reason = "candidate_limit"
                            break
                        if not batch:
                            # ANN exhaustion is NOT proof of global absence.
                            reason = "no_more_ann_candidates"
                            break
                    await db.execute(
                        text("SELECT set_config('enable_seqscan',:value,true)"),
                        {"value": previous_seqscan},
                    )
                    if found:
                        # Workflow index bounds this exact step to found IDs. It
                        # never silently scans all workflows to repair ANN recall.
                        ranked = text(f"""WITH scores AS (
                            SELECT DISTINCT ON (workflow_id) workflow_id,embedded_text AS matched_query,{distance} AS distance
                            FROM workflow_embeddings WHERE {s.predicate} AND workflow_id=ANY(CAST(:ids AS uuid[]))
                            ORDER BY workflow_id,{distance},embedded_text_sha256)
                            SELECT w.workflow_id,w.content_sha256,w.resource_revision,w.search_revision,w.file_path,w.name,scores.distance,scores.matched_query
                            FROM scores JOIN workflows w USING(workflow_id)
                            WHERE w.deleted_at IS NULL AND w.lifecycle='template' AND w.is_recommendable=true AND w.index_state='ready'
                            ORDER BY scores.distance,w.workflow_id""")
                        rows = (
                            (
                                await db.execute(
                                    ranked, {"vector": vector_text, "ids": found}
                                )
                            )
                            .mappings()
                            .all()
                        )
                        reranked = True
                items = []
                for row in rows:
                    score = 1 - float(row["distance"])
                    if score < self.similarity_threshold:
                        continue
                    document = await asyncio.to_thread(
                        WorkflowFileStore.read, row["file_path"]
                    )
                    # Public templates only; legacy execution objects are never
                    # reinterpreted under the public Workflow standard.
                    if document.get("workflow_version") != "2.0":
                        continue
                    items.append(
                        WorkflowSearchCandidate(
                            workflow_id=str(row["workflow_id"]),
                            content_sha256=row["content_sha256"],
                            resource_revision=row["resource_revision"],
                            search_revision=row["search_revision"],
                            name=row["name"],
                            similarity=score,
                            matched_query=row["matched_query"],
                            document=document,
                        )
                    )
                return result(items)
        except TimeoutError:
            reason = "timeout"
            return result()
        except SQLAlchemyError:
            reason = "database_unavailable"
            return result()
        except (OSError, ValueError):
            # A missing/corrupt shared-PV asset is an unavailable candidate, not
            # permission to synthesize its definition or claim search completeness.
            reason = "database_unavailable"
            return result()

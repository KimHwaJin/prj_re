-- exact
SELECT workflow_id, min(embedding <=> %(vector)s::vector) AS distance
FROM retrieval_queries WHERE is_active AND workflow_id >= %(minimum_workflow)s
GROUP BY workflow_id ORDER BY distance, workflow_id LIMIT %(top_k)s;

-- grouped candidates
WITH candidates AS MATERIALIZED (
SELECT workflow_id, embedding <=> %(vector)s::vector AS distance
FROM retrieval_queries WHERE is_active AND workflow_id >= %(minimum_workflow)s
ORDER BY embedding <=> %(vector)s::vector LIMIT %(limit)s
)
SELECT workflow_id, min(distance) AS distance, count(*) AS matched_rows
FROM candidates GROUP BY workflow_id
ORDER BY distance, workflow_id LIMIT %(top_k)s;

-- excluded Workflow search
SELECT workflow_id, embedding <=> %(vector)s::vector AS distance
FROM retrieval_queries WHERE is_active AND workflow_id >= %(minimum_workflow)s
AND NOT (workflow_id = ANY(%(excluded)s::integer[]))
ORDER BY embedding <=> %(vector)s::vector LIMIT %(limit)s;

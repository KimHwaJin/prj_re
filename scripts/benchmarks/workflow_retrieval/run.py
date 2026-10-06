"""Isolated pgvector grouped retrieval benchmark; never connects to service DBs.

Requires WORKFLOW_SEARCH_TEST_DSN targeting the isolated local workflow_search database.
Synthetic normalized vectors test retrieval mechanics, not language relevance.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import time

import numpy as np
import psycopg

DIMENSIONS = 768
WORKFLOWS = 100
TOP_K = 5
SEED = 9305

ELIGIBLE = "is_active AND workflow_id >= %(minimum_workflow)s"
EXACT = f"""SELECT workflow_id, min(embedding <=> %(vector)s::vector) AS distance
FROM retrieval_queries WHERE {ELIGIBLE}
GROUP BY workflow_id ORDER BY distance, workflow_id LIMIT %(top_k)s"""
NEAREST = f"""SELECT workflow_id, embedding <=> %(vector)s::vector AS distance
FROM retrieval_queries WHERE {ELIGIBLE}
AND NOT (workflow_id = ANY(%(excluded)s::integer[]))
ORDER BY embedding <=> %(vector)s::vector LIMIT %(limit)s"""
GROUPED = f"""WITH candidates AS MATERIALIZED (
SELECT workflow_id, embedding <=> %(vector)s::vector AS distance
FROM retrieval_queries WHERE {ELIGIBLE}
ORDER BY embedding <=> %(vector)s::vector LIMIT %(limit)s
)
SELECT workflow_id, min(distance) AS distance, count(*) AS matched_rows
FROM candidates GROUP BY workflow_id
ORDER BY distance, workflow_id LIMIT %(top_k)s"""


def normalized(vector):
    return (vector / np.linalg.norm(vector, axis=-1, keepdims=True)).astype(
        np.float32
    )


def corpus(queries_per_workflow, workflows=WORKFLOWS, dimensions=DIMENSIONS):
    rng = np.random.default_rng(SEED + workflows)
    anchor = np.zeros(dimensions)
    anchor[0] = 1
    directions = rng.normal(size=(workflows, dimensions))
    directions[:, 0] = 0
    directions = normalized(directions)
    angles = np.concatenate(
        (
            np.array([0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16]),
            rng.uniform(0.35, 1.2, workflows - 8),
        )
    )
    centers = normalized(
        np.cos(angles)[:, None] * anchor + np.sin(angles)[:, None] * directions
    )
    rows = []
    digest = hashlib.sha256()
    # Per-workflow seed keeps existing rows unchanged when increasing count.
    for workflow in range(workflows):
        local = np.random.default_rng(SEED + workflow * 1009 + workflows)
        noise = local.normal(size=(queries_per_workflow, dimensions))
        noise = normalized(noise) * 0.005
        vectors = normalized(centers[workflow] + noise)
        digest.update(vectors.tobytes())
        for vector in vectors:
            rows.append(
                (
                    workflow,
                    True,
                    json.dumps(vector.tolist(), separators=(",", ":")),
                )
            )
    queries = []
    query_rng = np.random.default_rng(SEED)
    for number in range(10):
        small = normalized(query_rng.normal(size=dimensions)) * 0.002
        queries.append(
            {
                "id": f"concentrated-{number}",
                "kind": "concentrated",
                "minimum_workflow": 0,
                "vector": normalized(anchor + small).tolist(),
            }
        )
    for number in range(5):
        center = normalized(centers[20 + number] + centers[40 + number])
        queries.append(
            {
                "id": f"boundary-{number}",
                "kind": "boundary",
                "minimum_workflow": 0,
                "vector": center.tolist(),
            }
        )
    for number in range(5):
        queries.append(
            {
                "id": f"filtered-{number}",
                "kind": "filtered",
                "minimum_workflow": 5,
                "vector": queries[number]["vector"],
            }
        )
    return rows, queries, digest.hexdigest()


def plan_totals(node):
    """Do not sum buffer counters across parent/child nodes (they overlap)."""
    scans = []

    def visit(item):
        if "Scan" in item["Node Type"]:
            scans.append(
                {
                    key: item.get(key)
                    for key in (
                        "Node Type",
                        "Index Name",
                        "Actual Rows",
                        "Actual Loops",
                        "Rows Removed by Filter",
                        "Shared Hit Blocks",
                        "Shared Read Blocks",
                    )
                }
            )
        for child in item.get("Plans", []):
            visit(child)

    visit(node["Plan"])
    return {
        "execution_ms": node["Execution Time"],
        "planning_ms": node["Planning Time"],
        "shared_hit_blocks": node["Plan"].get("Shared Hit Blocks", 0),
        "shared_read_blocks": node["Plan"].get("Shared Read Blocks", 0),
        "scans": scans,
    }


def settings(cursor, ef_search=100, iterative=True):
    cursor.execute("SET LOCAL jit = off")
    cursor.execute(
        "SELECT set_config('hnsw.ef_search', %s, true)", (str(ef_search),)
    )
    cursor.execute(
        "SELECT set_config('hnsw.iterative_scan', %s, true)",
        ("strict_order" if iterative else "off",),
    )
    cursor.execute("SET LOCAL hnsw.max_scan_tuples = 20000")
    cursor.execute("SET LOCAL hnsw.scan_mem_multiplier = 2")


def execute_method(connection, method, params, *, capture_plan=False):
    started = time.perf_counter()
    calls = []
    plans = []
    result = []
    fetched_rows = 0

    def fetch(cursor, sql, bindings):
        nonlocal fetched_rows
        cursor.execute(sql, bindings)
        found = cursor.fetchall()
        fetched_rows += len(found)
        calls.append(
            {
                "sql": "exact"
                if sql == EXACT
                else "nearest"
                if sql == NEAREST
                else "grouped",
                "limit": bindings.get("limit"),
                "excluded": list(bindings.get("excluded", [])),
                "returned": len(found),
            }
        )
        if capture_plan:
            cursor.execute(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, bindings
            )
            explained = cursor.fetchone()[0][0]
            plans.append(
                {
                    "call": calls[-1],
                    "totals": plan_totals(explained),
                    "plan": explained,
                }
            )
        return found

    with connection.transaction():
        with connection.cursor() as cursor:
            settings(
                cursor,
                ef_search=500 if method == "exclude_tuned" else 100,
                iterative=method != "exclude_default",
            )
            if method == "exclude_default":
                cursor.execute("SET LOCAL hnsw.ef_search = 40")
            if method == "exact":
                result = fetch(cursor, EXACT, params)
            elif method.startswith("overfetch_"):
                limit = int(method.removeprefix("overfetch_"))
                result = fetch(cursor, GROUPED, {**params, "limit": limit})
            elif method == "expand":
                for limit in (50, 100, 200, 1000):
                    result = fetch(cursor, GROUPED, {**params, "limit": limit})
                    if len(result) >= params["top_k"]:
                        break
            elif method.startswith("exclude_"):
                excluded = []
                for _ in range(params["top_k"]):
                    found = fetch(
                        cursor,
                        NEAREST,
                        {**params, "limit": 1, "excluded": excluded},
                    )
                    if not found:
                        break
                    workflow, distance = found[0]
                    if workflow in excluded:
                        raise AssertionError(
                            "Exclusion search returned a duplicate Workflow"
                        )
                    excluded.append(workflow)
                    result.append((workflow, distance))
                result.sort(key=lambda item: (item[1], item[0]))
            else:
                raise ValueError(method)
    duration = (time.perf_counter() - started) * 1000
    return {
        "result": [
            {"workflow_id": row[0], "distance": row[1]} for row in result
        ],
        "elapsed_ms": duration,
        "search_sql_calls": len(calls),
        "fetched_group_rows": fetched_rows,
        "calls": calls,
        "plans": plans,
        "contains_explain": capture_plan,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--counts", type=int, nargs="+", default=[5, 50, 500])
    parser.add_argument("--workflows", type=int, default=WORKFLOWS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--label", default="main")
    parser.add_argument("--active-only-index", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (
        args.workflows < 45
        or args.repeats < 1
        or any(count < 1 for count in args.counts)
    ):
        parser.error(
            "Use at least 45 Workflows, positive query counts and repeats"
        )
    dsn = os.environ["WORKFLOW_SEARCH_TEST_DSN"]
    # This executable drops its test table. Reject remote targets before connecting.
    parameters = psycopg.conninfo.conninfo_to_dict(dsn)
    if parameters.get("host") not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Benchmark requires an explicit loopback host")
    if parameters.get("hostaddr") not in (None, "127.0.0.1", "::1"):
        raise ValueError("Benchmark hostaddr must also be loopback")
    if parameters.get("dbname") != "workflow_search":
        raise ValueError(
            "Benchmark requires the isolated workflow_search database"
        )
    connection = psycopg.connect(dsn, autocommit=True)
    if connection.info.dbname != "workflow_search":
        raise ValueError(
            "Benchmark is only allowed on the isolated "
            "workflow_search "
            "database"
        )
    connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    environment = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "postgres": connection.execute("SELECT version()").fetchone()[0],
        "pgvector": connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone()[0],
        "numpy": np.__version__,
        "dimensions": DIMENSIONS,
        "workflows": args.workflows,
        "counts": args.counts,
        "repeats": args.repeats,
        "top_k": TOP_K,
        "seed": SEED,
        "jit": "off",
        "hnsw_ef_search": 100,
        "hnsw_tuned_ef_search": 500,
        "hnsw_max_scan_tuples": 20000,
        "hnsw_scan_mem_multiplier": 2,
        "hnsw_index": {
            "m": 16,
            "ef_construction": 128,
            "active_only": args.active_only_index,
        },
        "note": (
            "Synthetic float32 normalized vectors; warm sequential DB "
            "benchmark, no LLM/Executor/API/embedding "
            "HTTP."
        ),
    }
    (args.output / f"{args.label}-environment.json").write_text(
        json.dumps(environment, indent=2) + "\n"
    )
    (args.output / "queries.sql").write_text(
        "-- exact\n"
        + EXACT
        + ";\n\n-- grouped candidates\n"
        + GROUPED
        + ";\n\n-- excluded Workflow search\n"
        + NEAREST
        + ";\n"
    )
    methods = [
        "exact",
        "overfetch_50",
        "overfetch_200",
        "expand",
        "exclude_default",
        "exclude_iterative",
        "exclude_tuned",
    ]
    rng = random.Random(SEED)
    result_path = args.output / f"{args.label}-results.jsonl"
    with result_path.open("w") as output:
        for count in args.counts:
            print(
                f"corpus: {args.workflows} workflows × {count} queries",
                flush=True,
            )
            connection.execute("DROP TABLE IF EXISTS retrieval_queries")
            connection.execute(f"""CREATE TABLE retrieval_queries (
                query_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                workflow_id integer NOT NULL, is_active boolean NOT NULL,
                embedding vector({DIMENSIONS}) NOT NULL)""")
            rows, queries, checksum = corpus(count, args.workflows)
            with connection.cursor().copy(
                "COPY retrieval_queries (workflow_id, is_active, "
                "embedding) FROM "
                "STDIN"
            ) as copy:
                for row in rows:
                    copy.write_row(row)
            # Inactive rows sit near several boundary requests to exercise eligibility.
            connection.execute(
                "UPDATE retrieval_queries SET is_active=false WHERE "
                "workflow_id IN (20, 22, "
                "24)"
            )
            connection.execute(
                "CREATE INDEX ON retrieval_queries (workflow_id)"
            )
            started = time.perf_counter()
            connection.execute("SET maintenance_work_mem = '256MB'")
            connection.execute("SET max_parallel_maintenance_workers = 0")
            index_sql = (
                "CREATE INDEX retrieval_hnsw ON retrieval_queries USING "
                "hnsw (embedding vector_cosine_ops) WITH (m=16, "
                "ef_construction=128)"
            )
            if args.active_only_index:
                index_sql += " WHERE is_active"
            connection.execute(index_sql)
            index_ms = (time.perf_counter() - started) * 1000
            connection.execute("ANALYZE retrieval_queries")
            sizes = connection.execute(
                "SELECT pg_relation_size('retrieval_queries'),pg_rel"
                "ation_size('retrieval_hnsw')"
            ).fetchone()
            print(
                f"index ready: {index_ms / 1000:.2f}s, rows={len(rows)}",
                flush=True,
            )
            meta = {
                "count": count,
                "workflows": args.workflows,
                "rows": len(rows),
                "vector_sha256": checksum,
                "build_ms": index_ms,
                "table_bytes": sizes[0],
                "index_bytes": sizes[1],
                "inactive_workflows": [20, 22, 24],
                "queries": queries,
            }
            (args.output / f"{args.label}-corpus-{count}.json").write_text(
                json.dumps(meta, indent=2) + "\n"
            )
            for query in queries:
                params = {
                    "vector": json.dumps(query["vector"]),
                    "minimum_workflow": query["minimum_workflow"],
                    "top_k": TOP_K,
                }
                truth = execute_method(connection, "exact", params)["result"]
                # Warm all methods before the measured randomized repetitions.
                for method in methods:
                    execute_method(connection, method, params)
                for repeat in range(args.repeats):
                    order = methods.copy()
                    rng.shuffle(order)
                    for method in order:
                        measured = execute_method(connection, method, params)
                        identities = [
                            row["workflow_id"] for row in measured["result"]
                        ]
                        target = [row["workflow_id"] for row in truth]
                        measured.update(
                            label=args.label,
                            count=count,
                            workflows=args.workflows,
                            query_id=query["id"],
                            query_kind=query["kind"],
                            minimum_workflow=query["minimum_workflow"],
                            repeat=repeat,
                            method=method,
                            truth=truth,
                            unique_count=len(set(identities)),
                            recall_at_5=len(set(identities) & set(target))
                            / len(target),
                            ordered_match=identities == target,
                        )
                        output.write(json.dumps(measured) + "\n")
                        output.flush()
                if query["id"] in (
                    "concentrated-0",
                    "boundary-0",
                    "filtered-0",
                ):
                    plans = {
                        method: execute_method(
                            connection, method, params, capture_plan=True
                        )
                        for method in methods
                    }
                    (
                        args.output
                        / f"{args.label}-plans-{count}-{query['id']}.json"
                    ).write_text(json.dumps(plans, indent=2) + "\n")
            print(
                f"measurements complete: queries={len(queries)}, repeats={args.repeats}",
                flush=True,
            )
    connection.close()
    print("raw results:", result_path, flush=True)


if __name__ == "__main__":
    main()

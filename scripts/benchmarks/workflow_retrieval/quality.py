"""Evaluate the deployed WorkflowSearch on an isolated, loopback-only database.

Synthetic vectors isolate mechanics, not text relevance. Exact global search is
an offline reference only. No service DB, API, model or Executor is contacted.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import time
from uuid import UUID

import numpy as np
import psycopg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.engine import URL

from dtest.infrastructure.workflow_search.retrieval import WorkflowSearch
from dtest.infrastructure.file_storage.workflows import WorkflowFileStore
from dtest.settings.search import WorkflowSearchSettings

SEED = 9506


def norm(v):
    return (v / np.linalg.norm(v, axis=-1, keepdims=True)).astype(np.float32)


def make_corpus(kind, aliases, dimensions):
    if kind == "duplicate":
        # Match 094 precisely: two heavy groups + one isolated farther group.
        vectors = np.array([[1, 0.01, 0], [1, 0.2, 0], [1, 0.4, 0]], dtype=np.float32)
        owners = np.array(
            [w for w, n in enumerate([aliases, aliases, 1]) for _ in range(n)]
        )
        values = vectors[owners]
        queries = [
            {"id": "concentrated-0", "kind": "concentrated", "vector": [1, 0, 0]}
        ]
        return values, owners, queries, []
    count = 100
    rng = np.random.default_rng(SEED)
    anchor = np.zeros(dimensions)
    anchor[0] = 1
    directions = rng.normal(size=(count, dimensions))
    directions[:, 0] = 0
    directions = norm(directions)
    angles = np.concatenate((np.arange(1, 9) * 0.02, rng.uniform(0.35, 1.2, count - 8)))
    centers = norm(
        np.cos(angles)[:, None] * anchor + np.sin(angles)[:, None] * directions
    )
    blocks = []
    for w in range(count):
        local = np.random.default_rng(SEED + w * 1009)
        blocks.append(
            norm(centers[w] + norm(local.normal(size=(aliases, dimensions))) * 0.005)
        )
    values = np.concatenate(blocks)
    owners = np.repeat(np.arange(count), aliases)
    queries = []
    for i in range(4):
        queries.append(
            {
                "id": f"concentrated-{i}",
                "kind": "concentrated",
                "vector": norm(
                    anchor + norm(rng.normal(size=dimensions)) * 0.002
                ).tolist(),
            }
        )
    for i in range(4):
        queries.append(
            {
                "id": f"boundary-{i}",
                "kind": "boundary",
                "vector": norm(centers[20 + i] + centers[40 + i]).tolist(),
            }
        )
    return values, owners, queries, [20, 22, 24]


def profiles():
    # Every main point changes one field from current source defaults.
    return {
        "current": {},
        "ef40": {"ef_search": 40},
        "ef500": {"ef_search": 500},
        "ef1000": {"ef_search": 1000},
        "scan100k": {"max_scan_tuples": 100000},
        "memory4": {"scan_mem_multiplier": 4},
        "batch2": {"batch_size": 2},
        "batch256": {"batch_size": 256},
        "rounds16": {"max_rounds": 16},
        "budget_high": {
            "ef_search": 1000,
            "max_scan_tuples": 100000,
            "scan_mem_multiplier": 4,
        },
    }


class FixedEmbedding:
    def __init__(self, queries):
        self.values = {q["id"]: q["vector"] for q in queries}

    async def embed(self, texts):
        return [self.values[t] for t in texts]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def open_database(dsn):
    params = psycopg.conninfo.conninfo_to_dict(dsn)
    if params.get("host") not in {"127.0.0.1", "localhost", "::1"} or params.get(
        "hostaddr"
    ) not in {None, "127.0.0.1", "::1"}:
        raise ValueError("Explicit loopback only")
    if params.get("dbname") != "workflow_quality" or params.get("port") != "53609":
        raise ValueError("Requires isolated workflow_quality DB on port53609")
    c = psycopg.connect(dsn, autocommit=True)
    if c.info.dbname != "workflow_quality":
        raise ValueError("Unexpected target database")
    c.execute("CREATE EXTENSION IF NOT EXISTS vector")
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    return c, params


def load_corpus(
    c, policy, values, owners, inactive, build, files, *, collapse=False, index_m=16
):
    c.execute("DROP TABLE IF EXISTS workflow_embeddings")
    c.execute("DROP TABLE IF EXISTS workflows")
    # Keep the production search columns and expression/index shape exactly.
    # Authentication/registration writes are not part of this retrieval test.
    c.execute("""CREATE TABLE workflows(workflow_id uuid PRIMARY KEY,content_sha256 text,
        resource_revision int,search_revision int,file_path text,name text,deleted_at timestamptz,
        lifecycle text,is_recommendable boolean,index_state text)""")
    c.execute("""CREATE TABLE workflow_embeddings(workflow_id uuid,vector_values vector,
        is_active boolean,status text,model_space text,dimensions int,embedded_text text,
        embedded_text_sha256 text)""")
    c.execute("CREATE INDEX quality_workflow_owner ON workflow_embeddings(workflow_id)")
    document = {"workflow_version": "2.0", "fixture": "retrieval-only; no execution"}
    files.mkdir(parents=True, exist_ok=True)
    file = files / "immutable.json"
    write_json(file, document)
    content = hashlib.sha256(file.read_bytes()).hexdigest()
    with c.cursor().copy("COPY workflows FROM STDIN") as copy:
        for w in sorted(set(owners.tolist())):
            copy.write_row(
                (
                    UUID(int=w + 1),
                    content,
                    1,
                    1,
                    file.name,
                    str(w),
                    None,
                    "template",
                    True,
                    "ready",
                )
            )
    c.execute("SET max_parallel_maintenance_workers=0")
    c.execute("SET maintenance_work_mem='256MB'")
    started = time.perf_counter()
    index_sql = policy.index_sql().replace("m=16,", f"m={index_m},")
    if build == "online":
        c.execute(index_sql)
    with c.cursor().copy("COPY workflow_embeddings FROM STDIN") as copy:
        seen = set()
        for i, (v, w) in enumerate(zip(values, owners)):
            key = (int(w), v.tobytes())
            if collapse and key in seen:
                continue
            seen.add(key)
            copy.write_row(
                (
                    UUID(int=int(w) + 1),
                    json.dumps(v.tolist(), separators=(",", ":")),
                    int(w) not in inactive,
                    "ready",
                    policy.space,
                    policy.dimensions,
                    str(i),
                    hashlib.sha256(str(i).encode()).hexdigest(),
                )
            )
    if build == "bulk":
        c.execute(index_sql)
    build_ms = (time.perf_counter() - started) * 1000
    c.execute("ANALYZE workflow_embeddings")
    c.execute("ANALYZE workflows")
    return {
        "load_and_build_ms": build_ms,
        "rows": c.execute("SELECT count(*) FROM workflow_embeddings").fetchone()[0],
        "table_bytes": c.execute(
            "SELECT pg_total_relation_size('workflow_embeddings')"
        ).fetchone()[0],
        "hnsw_bytes": c.execute(
            "SELECT pg_relation_size(%s)", (policy.index_name,)
        ).fetchone()[0],
    }


def reference(c, policy, queries, values, owners, inactive, topk):
    refs = {}
    for q in queries:
        vec = np.array(q["vector"], dtype=np.float64)
        # Independent float64 cosine formula; pgvector uses float32 storage.
        v = values.astype(np.float64)
        distances = 1 - (v @ vec) / (np.linalg.norm(v, axis=1) * np.linalg.norm(vec))
        scores = {
            w: float(distances[owners == w].min())
            for w in sorted(set(owners.tolist()))
            if w not in inactive
        }
        target = sorted(scores, key=lambda w: (scores[w], w))[:topk]
        with c.transaction():
            c.execute("SET LOCAL enable_indexscan=off")
            c.execute("SET LOCAL enable_bitmapscan=off")
            sql = f"""SELECT workflow_id,min(vector_values::vector({policy.dimensions}) <=> %s::vector({policy.dimensions}))
                FROM workflow_embeddings WHERE {policy.predicate} GROUP BY workflow_id ORDER BY 2,1 LIMIT %s"""
            rows = c.execute(sql, (json.dumps(q["vector"]), topk)).fetchall()
        actual = [r[0].int - 1 for r in rows]
        if actual != target:
            raise AssertionError(
                ("Reference order disagreement", q["id"], actual, target)
            )
        if any(abs(float(d) - scores[w.int - 1]) > 2e-6 for w, d in rows):
            raise AssertionError("Cosine disagreement")
        refs[q["id"]] = {"target": target, "scores": scores}
    return refs


def plan_probe(c, policy, query, exclude=()):
    with c.transaction():
        c.execute("SET LOCAL enable_seqscan=off")
        for key, value in {
            "hnsw.iterative_scan": "relaxed_order",
            "hnsw.ef_search": policy.ef_search,
            "hnsw.max_scan_tuples": policy.max_scan_tuples,
            "hnsw.scan_mem_multiplier": policy.scan_mem_multiplier,
        }.items():
            c.execute("SELECT set_config(%s,%s,true)", (key, str(value)))
        distance = f"vector_values::vector({policy.dimensions}) <=> %s::vector({policy.dimensions})"
        sql = f"""SELECT workflow_id,{distance} AS distance FROM workflow_embeddings WHERE {policy.predicate}
          AND NOT (workflow_id=ANY(%s::uuid[])) ORDER BY {distance} LIMIT %s"""
        plan = c.execute(
            "EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + sql,
            (
                json.dumps(query["vector"]),
                [UUID(int=w + 1) for w in exclude],
                json.dumps(query["vector"]),
                policy.batch_size,
            ),
        ).fetchone()[0][0]
        if policy.index_name not in json.dumps(plan) or "Index Scan" not in json.dumps(
            plan
        ):
            raise AssertionError("Production ANN index was not used")
        return plan


async def measure(search, query, ref, label, repeat, case, topk):
    start = time.perf_counter()
    result = await search.search(query["id"])
    if result.diagnostics.termination not in {
        "candidate_limit",
        "round_limit",
        "no_more_ann_candidates",
    }:
        raise AssertionError(("Invalid measurement", result.diagnostics.model_dump()))
    elapsed = (time.perf_counter() - start) * 1000
    owners = [UUID(x.workflow_id).int - 1 for x in result.items]
    chosen = owners[:topk]
    if len(owners) != len(set(owners)):
        raise AssertionError("Duplicate workflow")
    if any(w not in ref["scores"] for w in owners):
        raise AssertionError("Ineligible workflow")
    if any(
        abs(x.similarity - (1 - ref["scores"][w])) > 2e-6
        for w, x in zip(owners, result.items)
    ):
        raise AssertionError("Candidate rerank mismatch")
    # This measures group TOP-K accuracy even when enough results are returned.
    return {
        "case": case,
        "profile": label,
        "query_id": query["id"],
        "query_kind": query["kind"],
        "repeat": repeat,
        "top_k": topk,
        "target": ref["target"],
        "returned": owners,
        "recall": len(set(chosen) & set(ref["target"])) / len(ref["target"]),
        "elapsed_ms": elapsed,
        "diagnostics": result.diagnostics.model_dump(),
        "ordered_match": chosen == ref["target"],
    }


async def main(args):
    c, params = open_database(os.environ["WORKFLOW_QUALITY_TEST_DSN"])
    out = args.output
    if out.exists() and any(out.iterdir()):
        c.close()
        raise ValueError("Use a new empty output directory; preserve prior evidence")
    out.mkdir(parents=True, exist_ok=True)
    WorkflowFileStore._root = staticmethod(lambda: (out / "fixtures").resolve())
    url = URL.create(
        "postgresql+psycopg",
        username=params["user"],
        password=params["password"],
        host="127.0.0.1",
        port=53609,
        database="workflow_quality",
    )
    engine = create_async_engine(url, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    records = []
    cases = []
    plans = {}
    environment = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "postgres": c.execute("SELECT version()").fetchone()[0],
        "pgvector": c.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone()[0],
        "work_mem": c.execute("SHOW work_mem").fetchone()[0],
        "seed": SEED,
        "source_sha256": hashlib.sha256(
            Path(
                __import__("dtest.infrastructure.workflow_search.retrieval", fromlist=["x"]).__file__
            ).read_bytes()
        ).hexdigest(),
        "numpy": np.__version__,
        "pool_size": 4,
        "max_overflow": 0,
        "rebuilds": args.rebuilds,
        "repeats": args.repeats,
        "index_m": args.index_m,
        "ef_construction": 128,
        "profiles": profiles(),
        "limitations": [
            "Synthetic vectors; no embedding HTTP, text accuracy, Agent, Executor or end-user latency.",
            "Exact global retrieval is offline evaluation only; runtime remains HNSW.",
            "Sequential warm samples; percentiles are descriptive, not production guarantees.",
            "Index build randomization is server-controlled; vector corpus is deterministic.",
        ],
    }
    write_json(out / "environment.json", environment)
    rng = random.Random(SEED)
    raw = (out / "results.jsonl").open("w")
    concurrent = []
    try:
        for kind, counts, dim, builds in [
            ("duplicate", [50, 100, 500], 3, ["online", "bulk"]),
            ("varied", [50, 500], 768, ["bulk"]),
        ]:
            for aliases in counts:
                values, owners, queries, inactive = make_corpus(kind, aliases, dim)
                topk = 3 if kind == "duplicate" else 5
                policy = WorkflowSearchSettings(
                    base_url="http://quality.invalid/v1",
                    model="synthetic",
                    dimensions=dim,
                    candidate_limit=3 if kind == "duplicate" else 20,
                )
                for build in builds:
                    for rebuild in range(args.rebuilds):
                        case = f"{kind}-{aliases}-{build}-{rebuild}"
                        print("loading " + case, flush=True)
                        meta = load_corpus(
                            c,
                            policy,
                            values,
                            owners,
                            inactive,
                            build,
                            out / "fixtures",
                            index_m=args.index_m,
                        )
                        refs = reference(
                            c, policy, queries, values, owners, inactive, topk
                        )
                        meta.update(
                            case=case,
                            kind=kind,
                            aliases=aliases,
                            dimensions=dim,
                            build=build,
                            rebuild=rebuild,
                            vector_sha256=hashlib.sha256(values.tobytes()).hexdigest(),
                            inactive=inactive,
                            queries=queries,
                            reference={k: v["target"] for k, v in refs.items()},
                        )
                        cases.append(meta)
                        write_json(out / "corpora.json", cases)
                        embedding = FixedEmbedding(queries)
                        searches = {
                            label: WorkflowSearch(
                                policy.model_copy(update=updates), embedding, factory
                            )
                            for label, updates in profiles().items()
                        }
                        for q in queries:
                            for search in searches.values():
                                await search.search(q["id"])
                            for repeat in range(args.repeats):
                                names = list(searches)
                                rng.shuffle(names)
                                for label in names:
                                    row = await measure(
                                        searches[label],
                                        q,
                                        refs[q["id"]],
                                        label,
                                        repeat,
                                        case,
                                        topk,
                                    )
                                    records.append(row)
                                    raw.write(json.dumps(row) + "\n")
                                    raw.flush()
                        for label in ["current", "budget_high"]:
                            q = queries[0]
                            plans[case + "-" + label + "-first"] = plan_probe(
                                c, searches[label].settings, q
                            )
                            plans[case + "-" + label + "-exclude"] = plan_probe(
                                c, searches[label].settings, q, [0]
                            )
                        # Limited concurrency comparison isolates DB admission+
                        # search work; this is not a sustained-load capacity test.
                        if kind == "varied" and aliases == 500 and rebuild == 0:
                            for label in ["current", "ef500", "budget_high"]:
                                for level in [1, 4, 10]:
                                    gate = asyncio.Semaphore(level)

                                    async def request(i):
                                        async with gate:
                                            q = queries[i % len(queries)]
                                            return await measure(
                                                searches[label],
                                                q,
                                                refs[q["id"]],
                                                label,
                                                i,
                                                case,
                                                topk,
                                            )

                                    c.execute("SELECT pg_stat_statements_reset()")
                                    began = time.perf_counter()
                                    observations = await asyncio.gather(
                                        *(request(i) for i in range(24))
                                    )
                                    wall_ms = (time.perf_counter() - began) * 1000
                                    dbstats = c.execute(
                                        "SELECT sum(total_exec_time),sum(shared_blks_hit),sum(shared_blks_read),sum(calls) FROM pg_stat_statements WHERE query LIKE '%workflow_embeddings%' AND query NOT LIKE '%pg_stat_statements%'"
                                    ).fetchone()
                                    concurrent.append(
                                        {
                                            "case": case,
                                            "profile": label,
                                            "concurrency": level,
                                            "requests": 24,
                                            "wall_ms": wall_ms,
                                            "db_execution_ms": float(dbstats[0] or 0),
                                            "shared_hit_blocks": int(dbstats[1] or 0),
                                            "shared_read_blocks": int(dbstats[2] or 0),
                                            "db_search_sql_calls": int(dbstats[3] or 0),
                                            "observations": observations,
                                        }
                                    )
                        print("measured " + case, flush=True)
                if kind == "duplicate" and aliases == 500:
                    # Diagnostic only: exact vector collapse per owner, no
                    # clustering, averaging, perturbation or source code change.
                    case = "duplicate-500-exact-collapse"
                    meta = load_corpus(
                        c,
                        policy,
                        values,
                        owners,
                        inactive,
                        "online",
                        out / "fixtures",
                        collapse=True,
                        index_m=args.index_m,
                    )
                    refs = reference(c, policy, queries, values, owners, inactive, topk)
                    meta.update(
                        case=case,
                        kind=kind,
                        aliases=aliases,
                        dimensions=dim,
                        build="online",
                        rebuild=0,
                        vector_sha256=hashlib.sha256(values.tobytes()).hexdigest(),
                        inactive=inactive,
                        queries=queries,
                        reference={k: v["target"] for k, v in refs.items()},
                        collapse_exact=True,
                    )
                    cases.append(meta)
                    write_json(out / "corpora.json", cases)
                    search = WorkflowSearch(policy, FixedEmbedding(queries), factory)
                    for repeat in range(args.repeats):
                        row = await measure(
                            search,
                            queries[0],
                            refs[queries[0]["id"]],
                            "current",
                            repeat,
                            case,
                            topk,
                        )
                        records.append(row)
                        raw.write(json.dumps(row) + "\n")
                        raw.flush()
                    plans[case] = plan_probe(c, policy, queries[0])
        write_json(out / "plans.json", plans)
        write_json(out / "concurrency.json", concurrent)
    finally:
        raw.close()
        c.close()
        await engine.dispose()
    grouped = defaultdict(list)
    for r in records:
        meta = next(m for m in cases if m["case"] == r["case"])
        grouped[
            (
                meta["kind"],
                meta["aliases"],
                meta["build"],
                bool(meta.get("collapse_exact")),
                r["query_kind"],
                r["profile"],
            )
        ].append(r)
    summary = []
    for key, group in sorted(grouped.items()):
        kind, aliases, build, collapse, qkind, profile = key
        summary.append(
            {
                "kind": kind,
                "aliases": aliases,
                "build": build,
                "collapse_exact": collapse,
                "query_kind": qkind,
                "profile": profile,
                "samples": len(group),
                "mean_recall": statistics.mean(r["recall"] for r in group),
                "full_recall_rate": statistics.mean(r["recall"] == 1 for r in group),
                "mean_results": statistics.mean(len(r["returned"]) for r in group),
                "mean_ms": statistics.mean(r["elapsed_ms"] for r in group),
                "p95_ms": float(np.percentile([r["elapsed_ms"] for r in group], 95)),
                "mean_rounds": statistics.mean(
                    r["diagnostics"]["rounds"] for r in group
                ),
            }
        )
    write_json(out / "summary.json", summary)
    print(
        json.dumps(
            {
                "records": len(records),
                "cases": len(cases),
                "plan_probes": len(plans),
                "concurrency_batches": len(concurrent),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--rebuilds", type=int, default=2)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--index-m", type=int, choices=[16, 32], default=16)
    args = p.parse_args()
    if not 1 <= args.rebuilds <= 5 or not 1 <= args.repeats <= 20:
        p.error("Invalid repetitions")
    asyncio.run(main(args))

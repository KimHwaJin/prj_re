"""Read-only boundary failure probe, after quality.py --index-m32 completes."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID
import time
from quality import open_database


def main(corpora, output):
    meta = next(
        m
        for m in reversed(json.loads(corpora.read_text()))
        if m["kind"] == "varied" and m["aliases"] == 500
    )
    q = next(q for q in meta["queries"] if q["id"] == "boundary-2")
    if meta["dimensions"] != 768:
        raise ValueError("This diagnostic expects the768D case")
    c, _ = open_database(os.environ["WORKFLOW_QUALITY_TEST_DSN"])
    try:
        assert (
            c.execute("SELECT count(*) FROM workflow_embeddings").fetchone()[0]
            == meta["rows"]
        )
        vector = json.dumps(q["vector"])
        space = c.execute(
            "SELECT model_space FROM workflow_embeddings LIMIT 1"
        ).fetchone()[0]
        predicate = (
            "is_active AND status='ready' AND model_space=%s AND "
            "dimensions=768"
        )
        out = {
            "case": meta["case"],
            "query": q,
            "index_options": c.execute(
                "SELECT reloptions FROM pg_class WHERE relname LIKE "
                "'ix_workflow_hnsw_%'"
            ).fetchall(),
            "max_scan_tuples": 1000000,
            "work_mem": "32MB",
            "scan_mem_multiplier": 16,
            "limit": 50000,
            "observations": [],
        }
        with c.transaction():
            c.execute("SET LOCAL enable_indexscan=off")
            ref = c.execute(
                f"SELECT workflow_id,min(vector_values::vector(768) <=> %s::vector(768)) FROM workflow_embeddings WHERE {predicate} GROUP BY workflow_id ORDER BY 2,1 LIMIT 5",
                (vector, space),
            ).fetchall()
            assert [w.int - 1 for w, d in ref] == meta["reference"][q["id"]]
            out["reference"] = [(str(w), d) for w, d in ref]
        for mode in ["off", "strict_order", "relaxed_order"]:
            for ef in [200, 1000]:
                for exclude in [False, True]:
                    with c.transaction():
                        c.execute("SET LOCAL enable_seqscan=off")
                        for name, value in [
                            ("hnsw.iterative_scan", mode),
                            ("hnsw.ef_search", ef),
                            ("hnsw.max_scan_tuples", 1000000),
                            ("hnsw.scan_mem_multiplier", 16),
                            ("work_mem", "32MB"),
                        ]:
                            c.execute(
                                "SELECT set_config(%s,%s,true)",
                                (name, str(value)),
                            )
                        sql = f"SELECT workflow_id FROM workflow_embeddings WHERE {predicate} AND NOT(workflow_id=ANY(%s::uuid[])) ORDER BY vector_values::vector(768) <=> %s::vector(768) LIMIT 50000"
                        params = (
                            space,
                            [UUID(int=43)] if exclude else [],
                            vector,
                        )
                        start = time.perf_counter()
                        rows = c.execute(sql, params).fetchall()
                        elapsed = (time.perf_counter() - start) * 1000
                        plan = c.execute(
                            "EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + sql,
                            params,
                        ).fetchone()[0][0]
                        assert "ix_workflow_hnsw_" in json.dumps(plan)
                        out["observations"].append(
                            {
                                "mode": mode,
                                "ef": ef,
                                "exclude42": exclude,
                                "rows": len(rows),
                                "groups": sorted(
                                    set(w.int - 1 for (w,) in rows)
                                ),
                                "elapsed_ms": elapsed,
                                "plan": plan,
                            }
                        )
    finally:
        c.close()
    output.write_text(json.dumps(out, indent=2) + "\n")
    print(
        json.dumps(
            [
                {k: v for k, v in x.items() if k != "plan"}
                for x in out["observations"]
            ]
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpora", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    main(args.corpora, args.output)

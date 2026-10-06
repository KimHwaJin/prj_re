"""Small duplicate-vector factor study; no service tables or runtime mutations."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import time
from quality import open_database


def main(out):
    c, _ = open_database(os.environ["WORKFLOW_QUALITY_TEST_DSN"])
    rows = []
    metadata = []
    plans = []
    profiles = {
        "current": (200, 20000, 2),
        "ef40": (40, 20000, 2),
        "ef500": (500, 20000, 2),
        "ef1000": (1000, 20000, 2),
        "high_budget": (1000, 100000, 4),
    }
    try:
        for count in [50, 100, 500, 1000]:
            for build in ["online", "bulk"]:
                for order in ["grouped", "shuffled"]:
                    for m, construction in [(16, 128), (32, 128)]:
                        for repeat in range(3):
                            case = f"{count}-{build}-{order}-m{m}-{repeat}"
                            c.execute("DROP TABLE IF EXISTS quality_probe")
                            c.execute(
                                "CREATE TABLE quality_probe(w int,v vector(3))"
                            )
                            ix = f"CREATE INDEX quality_probe_hnsw ON quality_probe USING hnsw(v vector_cosine_ops) WITH(m={m},ef_construction={construction})"
                            c.execute("SET max_parallel_maintenance_workers=0")
                            data = [
                                (w, f"[1,{a},0]")
                                for w, a, n in [
                                    (0, 0.01, count),
                                    (1, 0.2, count),
                                    (2, 0.4, 1),
                                ]
                                for _ in range(n)
                            ]
                            if order == "shuffled":
                                random.Random(9506 + repeat).shuffle(data)
                            start = time.perf_counter()
                            if build == "online":
                                c.execute(ix)
                            with c.cursor().copy(
                                "COPY quality_probe FROM STDIN"
                            ) as cp:
                                for item in data:
                                    cp.write_row(item)
                            if build == "bulk":
                                c.execute(ix)
                            build_ms = (time.perf_counter() - start) * 1000
                            c.execute("ANALYZE quality_probe")
                            reference = c.execute(
                                "SELECT w,count(*) FROM "
                                "quality_probe GROUP BY w ORDER BY "
                                "w"
                            ).fetchall()
                            assert reference == [
                                (0, count),
                                (1, count),
                                (2, 1),
                            ]
                            metadata.append(
                                {
                                    "case": case,
                                    "count": count,
                                    "build": build,
                                    "order": order,
                                    "m": m,
                                    "ef_construction": construction,
                                    "repeat": repeat,
                                    "load_build_ms": build_ms,
                                    "index_bytes": c.execute(
                                        "SELECT pg_relation_size('qu"
                                        "ality_probe_hnsw')"
                                    ).fetchone()[0],
                                    "index_options": c.execute(
                                        "SELECT reloptions FROM "
                                        "pg_class WHERE "
                                        "oid='quality_probe_hnsw'::r"
                                        "egclass"
                                    ).fetchone()[0],
                                    "reference": reference,
                                }
                            )
                            for label, (ef, scan, mem) in profiles.items():
                                for excluded in [False, True]:
                                    with c.transaction():
                                        c.execute(
                                            "SET LOCAL enable_seqscan=off"
                                        )
                                        c.execute(
                                            "SET LOCAL hnsw.iterativ"
                                            "e_scan=relaxed_order"
                                        )
                                        for key, val in [
                                            ("hnsw.ef_search", ef),
                                            ("hnsw.max_scan_tuples", scan),
                                            ("hnsw.scan_mem_multiplier", mem),
                                        ]:
                                            c.execute(
                                                (
                                                    "SELECT set_config(%"
                                                    "s,%s,true)"
                                                ),
                                                (key, str(val)),
                                            )
                                        sql = (
                                            "SELECT w FROM quality_probe "
                                            + (
                                                "WHERE w<>0 "
                                                if excluded
                                                else ""
                                            )
                                            + (
                                                "ORDER BY v <=> "
                                                "'[1,0,0]' LIMIT "
                                                "5000"
                                            )
                                        )
                                        start = time.perf_counter()
                                        found = c.execute(sql).fetchall()
                                        elapsed = (
                                            time.perf_counter() - start
                                        ) * 1000
                                        groups = sorted(
                                            set(item[0] for item in found)
                                        )
                                        rows.append(
                                            {
                                                "case": case,
                                                "profile": label,
                                                "excluded0": excluded,
                                                "ef_search": ef,
                                                "max_scan_tuples": scan,
                                                "memory_multiplier": mem,
                                                "groups": groups,
                                                "returned_rows": len(found),
                                                "elapsed_ms": elapsed,
                                            }
                                        )
                                        if (
                                            label == "current"
                                            and count == 500
                                            and order == "grouped"
                                            and repeat == 0
                                        ):
                                            plan = c.execute(
                                                (
                                                    "EXPLAIN(ANALYZE,BUF"
                                                    "FERS,FORMAT JSON) "
                                                )
                                                + sql
                                            ).fetchone()[0][0]
                                            assert (
                                                "quality_probe_hnsw"
                                                in json.dumps(plan)
                                            )
                                            plans.append(
                                                {
                                                    "case": case,
                                                    "excluded0": excluded,
                                                    "plan": plan,
                                                }
                                            )
        # Exact duplicate collapse is a diagnostic control; never discard text
        # aliases or merge merely close vectors in production.
        c.execute("DROP TABLE IF EXISTS quality_probe")
        c.execute("CREATE TABLE quality_probe(w int,v vector(3))")
        c.execute(
            "CREATE INDEX quality_probe_hnsw ON quality_probe USING "
            "hnsw(v vector_cosine_ops) WITH(m=16,ef_construction=128"
            ")"
        )
        c.execute(
            "INSERT INTO quality_probe VALUES(0,'[1,.01,0]'),(1,'[1,"
            ".2,0]'),(2,'[1,.4,0]')"
        )
        with c.transaction():
            c.execute("SET LOCAL enable_seqscan=off")
            collapse = c.execute(
                "SELECT w FROM quality_probe ORDER BY v <=> '[1,0,0]'"
            ).fetchall()
            assert collapse == [(0,), (1,), (2,)]
    finally:
        c.close()
    artifact = {
        "metadata": metadata,
        "observations": rows,
        "plans": plans,
        "collapsed_groups": [w[0] for w in collapse],
        "profiles": profiles,
        "limitations": [
            (
                "Three-dimensional identical-vector controls, not "
                "natural language."
            ),
            (
                "Server HNSW graph randomness is not seed-controlled; "
                "repeats are independent "
                "builds."
            ),
            (
                "No iteration limit in application; LIMIT5000 exceeds "
                "entire2001-row "
                "corpus."
            ),
        ],
    }
    out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(
        json.dumps(
            {
                "builds": len(metadata),
                "observations": len(rows),
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    main(p.parse_args().output)

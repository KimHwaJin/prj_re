"""Recalculate group recall, cosine scores and counts from persisted raw evidence."""

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
from uuid import UUID
import numpy as np


def read(path):
    return json.loads(path.read_text())


def verify(folder, runner):
    spec = importlib.util.spec_from_file_location("quality_corpus", runner)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = read(folder / "environment.json")
    corpora = read(folder / "corpora.json")
    records = [
        json.loads(x)
        for x in (folder / "results.jsonl").read_text().splitlines()
    ]
    expected = (
        env["rebuilds"]
        * env["repeats"]
        * len(env["profiles"])
        * (3 * 2 + 2 * 8)
        + env["repeats"]
    )
    assert len(records) == expected, (len(records), expected)
    source = Path(
        __import__(
            "dtest.infrastructure.workflow_search.retrieval", fromlist=["x"]
        ).__file__
    )
    assert (
        hashlib.sha256(source.read_bytes()).hexdigest() == env["source_sha256"]
    )
    groups = defaultdict(list)
    for r in records:
        groups[r["case"]].append(r)
    checks = 0
    for meta in corpora:
        values, owners, queries, inactive = module.make_corpus(
            meta["kind"], meta["aliases"], meta["dimensions"]
        )
        assert (
            hashlib.sha256(values.tobytes()).hexdigest()
            == meta["vector_sha256"]
        )
        assert queries == meta["queries"] and inactive == meta["inactive"]
        v = values.astype(np.float64)
        norms = np.linalg.norm(v, axis=1)
        maps = {}
        for q in queries:
            vector = np.array(q["vector"], dtype=np.float64)
            distance = 1 - (v @ vector) / (norms * np.linalg.norm(vector))
            scores = {
                int(w): float(distance[owners == w].min())
                for w in set(owners.tolist())
                if w not in inactive
            }
            topk = 3 if meta["kind"] == "duplicate" else 5
            target = sorted(scores, key=lambda w: (scores[w], w))[:topk]
            assert target == meta["reference"][q["id"]]
            maps[q["id"]] = target
            checks += 1
        for r in groups[meta["case"]]:
            assert r["target"] == maps[r["query_id"]]
            actual = r["returned"]
            top = actual[: r["top_k"]]
            assert len(actual) == len(set(actual)) and not set(actual) & set(
                inactive
            )
            assert len(actual) <= (3 if meta["kind"] == "duplicate" else 20)
            assert r["recall"] == len(set(top) & set(r["target"])) / len(
                r["target"]
            )
            assert r["ordered_match"] == (top == r["target"])
            assert r["elapsed_ms"] >= 0
            d = r["diagnostics"]
            policy = env["profiles"].get(r["profile"], {})
            assert d["rounds"] <= policy.get("max_rounds", 8)
            assert d["ann_rows"] <= d["rounds"] * policy.get("batch_size", 64)
            assert (
                d["approximate"]
                and d["reranked"]
                and d["distinct_candidates"] == len(actual)
            )
            assert d["termination"] in {
                "candidate_limit",
                "round_limit",
                "no_more_ann_candidates",
            }
            checks += 1
        del v, values
    summary = read(folder / "summary.json")
    for s in summary:
        g = [
            r
            for r in records
            if r["profile"] == s["profile"]
            and r["query_kind"] == s["query_kind"]
            and any(
                m["case"] == r["case"]
                and m["kind"] == s["kind"]
                and m["aliases"] == s["aliases"]
                and m["build"] == s["build"]
                and bool(m.get("collapse_exact")) == s["collapse_exact"]
                for m in corpora
            )
        ]
        assert len(g) == s["samples"]
        for key, expected_value in [
            ("mean_recall", statistics.mean(r["recall"] for r in g)),
            ("mean_results", statistics.mean(len(r["returned"]) for r in g)),
            ("mean_ms", statistics.mean(r["elapsed_ms"] for r in g)),
            ("p95_ms", float(np.percentile([r["elapsed_ms"] for r in g], 95))),
        ]:
            assert abs(s[key] - expected_value) < 1e-9
        checks += 1
    plans = read(folder / "plans.json")
    assert len(plans) == len(corpora) * 4 - 3
    for p in plans.values():
        scans = []

        def walk(node):
            if node["Node Type"] == "Index Scan":
                scans.append(node)
            for child in node.get("Plans", []):
                walk(child)

        walk(p["Plan"])
        assert any(
            s.get("Index Name", "").startswith("ix_workflow_hnsw_")
            for s in scans
        )
        assert p["Execution Time"] >= 0
        checks += 1
    batches = read(folder / "concurrency.json")
    assert len(batches) == 9
    for b in batches:
        assert len(b["observations"]) == b["requests"] == 24
        assert b["wall_ms"] > 0 and b["db_execution_ms"] > 0
        assert b["db_search_sql_calls"] == sum(
            r["diagnostics"]["rounds"] + 1 for r in b["observations"]
        )
        for r in b["observations"]:
            assert r["recall"] == len(
                set(r["returned"][: r["top_k"]]) & set(r["target"])
            ) / len(r["target"])
        checks += 1
    receipt = {
        "status": "passed",
        "records": len(records),
        "corpora": len(corpora),
        "plan_probes": len(plans),
        "concurrency_batches": len(batches),
        "independent_checks": checks,
        "file_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.glob("*.json*")
            if p.name != "verification.json"
        },
        "source_unchanged": True,
        "limits": [
            (
                "Synthetic corpus validates search mechanics, not "
                "language or production "
                "quality."
            ),
            (
                "A 24-request burst is not sustained-load capacity; DB "
                "execution time is not CPU "
                "time."
            ),
        ],
    }
    (folder / "verification.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print(json.dumps(receipt))


def verify_probes(duplicate, boundary, output):
    data = read(duplicate)
    metadata = {m["case"]: m for m in data["metadata"]}
    assert len(metadata) == 96 and len(data["observations"]) == 960
    checks = 0
    for m in metadata.values():
        assert m["reference"] == [[0, m["count"]], [1, m["count"]], [2, 1]]
        assert "m=" + str(m["m"]) in m["index_options"]
        assert "ef_construction=128" in m["index_options"]
        assert m["index_bytes"] > 0 and m["load_build_ms"] > 0
        checks += 1
    for row in data["observations"]:
        meta = metadata[row["case"]]
        allowed = {1, 2} if row["excluded0"] else {0, 1, 2}
        assert row["groups"] == sorted(set(row["groups"]))
        assert set(row["groups"]) <= allowed
        assert (
            len(row["groups"])
            <= row["returned_rows"]
            <= (
                meta["count"] + 1
                if row["excluded0"]
                else 2 * meta["count"] + 1
            )
        )
        assert row["elapsed_ms"] >= 0
        checks += 1
    assert data["collapsed_groups"] == [0, 1, 2]
    assert len(data["plans"]) == 8
    for p in data["plans"]:
        assert "quality_probe_hnsw" in json.dumps(p["plan"])
        checks += 1
    point = read(boundary)
    assert len(point["observations"]) == 12
    assert {UUID(w).int - 1 for w, score in point["reference"]} == {
        0,
        42,
        2,
        3,
        1,
    }
    assert point["limit"] == 50000 and point["max_scan_tuples"] == 1000000
    assert any(
        "m=32" in options for row in point["index_options"] for options in row
    )
    for row in point["observations"]:
        assert "ix_workflow_hnsw_" in json.dumps(row["plan"])
        assert len(row["groups"]) == len(set(row["groups"]))
        if row["exclude42"]:
            assert 42 not in row["groups"]
        assert row["elapsed_ms"] >= 0 and row["rows"] >= len(row["groups"])
        checks += 1
    result = {
        "status": "passed",
        "independent_checks": checks,
        "builds": 96,
        "duplicate_observations": 960,
        "boundary_observations": 12,
        "sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [duplicate, boundary]
        },
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--duplicate-probe", type=Path)
    parser.add_argument("--boundary-probe", type=Path)
    parser.add_argument("--probe-verification", type=Path)
    parser.add_argument(
        "--runner", type=Path, default=Path(__file__).with_name("quality.py")
    )
    args = parser.parse_args()
    if args.results:
        verify(args.results, args.runner)
    elif (
        args.duplicate_probe
        and args.boundary_probe
        and args.probe_verification
    ):
        verify_probes(
            args.duplicate_probe, args.boundary_probe, args.probe_verification
        )
    else:
        parser.error("Provide --results or all three probe options")

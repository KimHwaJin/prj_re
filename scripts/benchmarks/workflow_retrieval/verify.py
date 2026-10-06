"""Independent validation of raw benchmark results and SQL exact reference.

Recomputes aggregates, result predicates and vector distances using NumPy;
does not trust the metrics emitted by the benchmark runner.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

import numpy as np


def load(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--runner", type=Path, default=Path(__file__).with_name("run.py")
    )
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "retrieval_runner_fixture", args.runner
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = args.results
    records = []
    validations = []
    independent_checks = 0
    for environment_path in sorted(folder.glob("*-environment.json")):
        label = environment_path.name.removesuffix("-environment.json")
        environment = load(environment_path)
        current = [
            json.loads(line)
            for line in (folder / f"{label}-results.jsonl")
            .read_text()
            .splitlines()
        ]
        expected = len(environment["counts"]) * 20 * environment["repeats"] * 7
        assert len(current) == expected, (label, len(current), expected)
        for count in environment["counts"]:
            meta = load(folder / f"{label}-corpus-{count}.json")
            generated, _, checksum = module.corpus(
                count, environment["workflows"], environment["dimensions"]
            )
            assert checksum == meta["vector_sha256"]
            values = np.array(
                [json.loads(row[2]) for row in generated], dtype=np.float64
            )
            owners = np.array([row[0] for row in generated])
            # Independent cosine formula, not the pgvector operator.
            norms = np.linalg.norm(values, axis=1)
            reference = {}
            distance_maps = {}
            for query in meta["queries"]:
                vector = np.array(query["vector"], dtype=np.float64)
                distances = 1 - (values @ vector) / (
                    norms * np.linalg.norm(vector)
                )
                per_workflow = {
                    int(owner): float(distances[owners == owner].min())
                    for owner in range(environment["workflows"])
                    if owner >= query["minimum_workflow"]
                    and owner not in meta["inactive_workflows"]
                }
                order = sorted(
                    per_workflow,
                    key=lambda owner: (per_workflow[owner], owner),
                )[: environment["top_k"]]
                reference[query["id"]] = order
                distance_maps[query["id"]] = per_workflow
                independent_checks += 1
            subset = [row for row in current if row["count"] == count]
            for row in subset:
                target = [item["workflow_id"] for item in row["truth"]]
                actual = [item["workflow_id"] for item in row["result"]]
                assert target == reference[row["query_id"]], (
                    label,
                    count,
                    row["query_id"],
                    target,
                    reference[row["query_id"]],
                )
                assert len(actual) == len(set(actual)) <= environment["top_k"]
                assert all(
                    owner >= row["minimum_workflow"]
                    and owner not in meta["inactive_workflows"]
                    for owner in actual
                )
                assert row["unique_count"] == len(actual)
                recall = len(set(actual) & set(target)) / len(target)
                assert row["recall_at_5"] == recall
                assert row["ordered_match"] == (actual == target)
                assert row["search_sql_calls"] == len(row["calls"])
                assert not row["contains_explain"] and not row["plans"]
                scores = [item["distance"] for item in row["result"]]
                assert scores == sorted(scores)
                for item in row["result"]:
                    assert (
                        np.isfinite(item["distance"])
                        and -2e-6 <= item["distance"] <= 2 + 2e-6
                    )
                    assert (
                        item["distance"] + 2e-6
                        >= distance_maps[row["query_id"]][item["workflow_id"]]
                    )
                for item in row["truth"]:
                    assert (
                        abs(
                            item["distance"]
                            - distance_maps[row["query_id"]][
                                item["workflow_id"]
                            ]
                        )
                        < 2e-6
                    )
                if row["method"].startswith("exclude_"):
                    found = []
                    for call in row["calls"]:
                        assert set(call["excluded"]) == set(found)
                        if call["returned"]:
                            # Raw results are score-sorted after retrieval. Calls only
                            # record exclusions, so the next call reveals the new owner.
                            index = row["calls"].index(call)
                            if index + 1 < len(row["calls"]):
                                next_excluded = row["calls"][index + 1][
                                    "excluded"
                                ]
                                added = set(next_excluded) - set(found)
                                assert len(added) == 1
                                found.extend(added)
                    assert len(row["calls"]) <= environment["top_k"]
                independent_checks += 1
            del generated, values, owners, norms
        records.extend(current)
        validations.append(
            {
                "label": label,
                "rows": len(current),
                "record_sha256": hashlib.sha256(
                    (folder / f"{label}-results.jsonl").read_bytes()
                ).hexdigest(),
            }
        )
    grouped = defaultdict(list)
    for row in records:
        grouped[
            (
                row["label"],
                row["workflows"],
                row["count"],
                row["query_kind"],
                row["method"],
            )
        ].append(row)
    summary = []
    for key, rows in sorted(grouped.items()):
        label, workflows, count, kind, method = key
        elapsed = [row["elapsed_ms"] for row in rows]
        summary.append(
            {
                "label": label,
                "workflows": workflows,
                "queries_per_workflow": count,
                "query_kind": kind,
                "method": method,
                "observations": len(rows),
                "mean_ms": statistics.mean(elapsed),
                "median_ms": statistics.median(elapsed),
                "p95_ms": float(np.percentile(elapsed, 95)),
                "mean_workflows": statistics.mean(
                    row["unique_count"] for row in rows
                ),
                "full_count_rate": statistics.mean(
                    row["unique_count"] == 5 for row in rows
                ),
                "mean_recall_at_5": statistics.mean(
                    row["recall_at_5"] for row in rows
                ),
                "exact_order_rate": statistics.mean(
                    row["ordered_match"] for row in rows
                ),
                "mean_search_sql_calls": statistics.mean(
                    row["search_sql_calls"] for row in rows
                ),
            }
        )
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    receipt = {
        "status": "passed",
        "independent_checks": independent_checks,
        "raw_record_count": len(records),
        "datasets": validations,
        "limitations": [
            (
                "Synthetic vectors validate retrieval mechanics, not "
                "natural-language semantic "
                "relevance."
            ),
            (
                "Distances verified for exact references; approximate "
                "search may miss the globally best query within a "
                "returned Workflow."
            ),
            (
                "p95 is a descriptive percentile of this small warm "
                "sequential sample, not production tail "
                "latency."
            ),
        ],
    }
    (folder / "verification.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()

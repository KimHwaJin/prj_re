"""Compare fixed-source runtime_profile trials; preserve raw evidence and checks.

This is a focused 1/10-user benchmark, not a production capacity estimate.
"""

import argparse
import collections
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "profile_analysis", ROOT / "scripts/benchmarks/runtime_profile/analyze.py"
)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def coverage(intervals):
    points = sorted({t for pair in intervals for t in pair})
    return sum(
        end - start
        for start, end in zip(points, points[1:])
        if any(left <= start and end <= right for left, right in intervals)
    )


def check(raw, result, shutdown):
    server = raw["server"]
    n = result["users"]
    assert result["source_commit"] == raw["config"]["commit"]
    assert n == len(raw["scenarios"]) and len(raw["stages"]) == 4 * n
    assert math.isclose(
        result["per_user"]["total_s"]["mean"],
        statistics.mean(x["ms"] / 1000 for x in raw["scenarios"]),
        abs_tol=1e-8,
    )
    assert math.isclose(
        result["per_user"]["queue_s"]["mean"],
        sum(x["queue_ms"] for x in raw["database"]["runs"]) / 1000 / n,
        abs_tol=1e-8,
    )
    model_time = 0
    for worker in server["workers"]:
        models = [
            x for x in server["models"] if x["run_id"] == worker["run_id"]
        ]
        assert all(
            worker["start"] <= x["start"] < x["end"] <= worker["end"]
            for x in models
        )
        model_time += coverage([(x["start"], x["end"]) for x in models])
    assert math.isclose(
        result["per_user"]["llm_s"]["mean"], model_time / n, abs_tol=1e-8
    )
    assert math.isclose(
        result["per_user"]["internal_s"]["mean"],
        (sum(x["ms"] for x in server["workers"]) / 1000 - model_time) / n,
        abs_tol=1e-8,
    )
    assert result["sql_by_actor"]["worker"]["count"] == sum(
        x["kind"] == "worker" for x in server["sql"]
    )
    assert result["commit_calls_by_actor"]["worker"] == sum(
        x["kind"] == "worker" for x in server["commits"]
    )
    assert len({x["attempt_id"] for x in raw["stages"]}) == 4 * n
    assert len({x["run_id"] for x in raw["stages"]}) == n
    assert (
        sum(x["operation"] == "graph_build" for x in server["resources"]) == 1
    )
    assert (
        sum(x["operation"] == "pool_construct" for x in server["resources"])
        == 2
    )
    assert all(
        not x["forced_kill"] and x["returncode"] in (0, -15)
        for x in shutdown.values()
    )
    return {
        "users": n,
        "source_ref": raw["config"]["commit"],
        "status": "passed",
        "complete": n,
        "attempts": 4 * n,
        "models": 4 * n,
        "worker_sql_per_user": result["sql_by_actor"]["worker"]["count"] / n,
        "worker_commit_calls_per_user": result["commit_calls_by_actor"][
            "worker"
        ]
        / n,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, action="append", required=True)
    parser.add_argument("--after", type=Path, action="append", required=True)
    parser.add_argument("--users", type=int, nargs="+", default=[1, 10])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    assert len(args.before) == len(args.after)
    trials = []
    checked = []
    manifest = []
    for variant, folders in [("before", args.before), ("after", args.after)]:
        for repeat, folder in enumerate(folders, 1):
            paths = sorted(folder.glob("flow-*/raw.json"))
            assert sorted(
                json.loads(path.read_bytes())["config"]["users"]
                for path in paths
            ) == sorted(args.users), folder
            for path in paths:
                data = path.read_bytes()
                raw = json.loads(data)
                result = analysis.analyze(path)
                shutdown = json.loads(
                    (path.parent / "shutdown.json").read_text()
                )
                validation = check(raw, result, shutdown)
                rel = Path("raw") / f"{variant}-r{repeat}" / path.parent.name
                dest = args.output / rel
                dest.mkdir(parents=True, exist_ok=True)
                # Payloads are synthetic. Normalize only local filesystem prefixes.
                normalized = (
                    data.decode()
                    .replace(str(Path.home()), "<home>")
                    .replace(str(folder), "<trial-root>")
                    .encode()
                )
                (dest / "raw.json.gz").write_bytes(
                    gzip.compress(normalized, mtime=0)
                )
                (dest / "shutdown.json").write_text(
                    json.dumps(shutdown, indent=2) + "\n"
                )
                result.update(
                    variant=variant,
                    repeat=repeat,
                    raw=str(rel / "raw.json.gz"),
                    messages=raw["database"]["messages"],
                    logs=raw["database"]["logs"],
                )
                trials.append(result)
                checked.append(
                    {**validation, "variant": variant, "repeat": repeat}
                )
                manifest.append(
                    {
                        "raw": result["raw"],
                        "original_sha256": hashlib.sha256(data).hexdigest(),
                        "normalized_sha256": hashlib.sha256(
                            normalized
                        ).hexdigest(),
                    }
                )
    grouped = collections.defaultdict(list)
    for row in trials:
        grouped[row["users"]].append(row)
    comparisons = []
    for users, rows in sorted(grouped.items()):
        entry = {"users": users}
        for variant in ("before", "after"):
            selected = [x for x in rows if x["variant"] == variant]
            assert len(selected) == len(args.before) == len(args.after)
            values = {
                "repeats": len(selected),
                "complete_users": sum(x["complete_users"] for x in selected),
            }
            for key in (
                "total_s",
                "queue_s",
                "llm_s",
                "internal_s",
                "projection_s",
                "delivery_s",
            ):
                values[key] = statistics.mean(
                    x["per_user"][key]["mean"] for x in selected
                )
                values[key + "_trial_means"] = [
                    x["per_user"][key]["mean"] for x in selected
                ]
            values["batch_throughput_users_s"] = values[
                "complete_users"
            ] / sum(x["elapsed_s"] for x in selected)
            values["worker_sql_per_user"] = statistics.mean(
                x["sql_by_actor"]["worker"]["count"] / users for x in selected
            )
            values["worker_commit_calls_per_user"] = statistics.mean(
                x["commit_calls_by_actor"]["worker"] / users for x in selected
            )
            entry[variant] = values
        assert len({x["messages"] / users for x in rows}) == 1
        assert len({x["logs"] / users for x in rows}) == 1
        entry["reduction_percent"] = {
            key: 100 * (1 - entry["after"][key] / entry["before"][key])
            for key in (
                "total_s",
                "internal_s",
                "projection_s",
                "worker_sql_per_user",
                "worker_commit_calls_per_user",
            )
        }
        comparisons.append(entry)
    for name, data in [
        ("trials.json", trials),
        ("checks.json", checked),
        ("manifest.json", manifest),
        ("comparison.json", comparisons),
    ]:
        (args.output / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
    print(json.dumps(comparisons, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

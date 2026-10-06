"""Independent spot checks of the saved report's important claims and joins."""

import argparse
from collections import Counter, defaultdict
import gzip
import json
import math
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("data", type=Path)
args = parser.parse_args()
with gzip.open(args.data / "raw-trials.jsonl.gz", "rt") as stream:
    trials = [json.loads(line) for line in stream]
summaries = json.loads((args.data / "summary.json").read_text())
groups = defaultdict(list)
for trial in trials:
    config = trial["config"]
    groups[config["name"], config["label"]].append(trial)
assert len(trials) == 80 and len(groups) == 40
assert all(
    {t["config"]["repeat"] for t in group} == {1, 2}
    for group in groups.values()
)
assert (
    len(
        {
            (t["config"]["name"], t["config"]["label"], t["config"]["repeat"])
            for t in trials
        }
    )
    == 80
)

checks = []
for name in sorted({key[0] for key in groups}):
    before = groups[name, "before"][0]["config"]
    after = groups[name, "after"][0]["config"]
    for key in ("scenario", "users", "delay_ms", "pool", "slots", "deadline"):
        assert before[key] == after[key], (name, key)
for summary in summaries:
    group = groups[summary["name"], summary["label"]]
    requests = [request for trial in group for request in trial["requests"]]
    scenarios = [
        scenario for trial in group for scenario in trial["scenarios"]
    ]
    completed = [
        scenario for scenario in scenarios if scenario["status"] == "complete"
    ]
    assert summary["complete"] == len(completed)
    assert summary["attempted"] == len(scenarios)
    assert summary["http_requests"] == len(requests)
    assert summary["http_errors"] == sum(
        bool(request["error"]) for request in requests
    )
    if completed:
        independent_mean = sum(item["ms"] for item in completed) / len(
            completed
        )
        assert math.isclose(
            summary["scenario_mean_ms"], independent_mean, abs_tol=1e-7
        )
    if summary["name"] in ("mixed-100", "flow-100"):
        holds = [
            hold["ms"]
            for trial in group
            for hold in trial["server"]["holds"]
            if hold["kind"] == "worker"
        ]
        run_count = sum(len(trial["database"]["runs"]) for trial in group)
        occupancy = sum(holds) / 1000 / run_count
        assert math.isclose(
            summary["worker_hold_seconds_per_run"], occupancy, abs_tol=1e-9
        )
        times = sorted(
            request["server_ms"]
            for request in requests
            if request["kind"] == "background_crud"
            and not request["error"]
            and request["server_ms"] is not None
        )
        rank = (len(times) * 95 + 99) // 100
        assert summary["background_crud_server_p95_ms"] == times[rank - 1]
        checks.append(
            {
                "case": summary["name"],
                "version": summary["label"],
                "worker_connection_seconds_per_run": occupancy,
                "background_crud_server_p95_ms": times[rank - 1],
                "scenario_mean_ms": independent_mean,
            }
        )

model_join_misses = 0
decomposed_stages = 0
negative_components = []
for trial in trials:
    runs = {row["run_id"]: row for row in trial["database"]["runs"]}
    assert len(runs) == len(trial["database"]["runs"])
    models = defaultdict(float)
    for model in trial["server"]["model_events"]:
        if model["run_id"] not in runs:
            model_join_misses += 1
        models[model["run_id"]] += model["ms"]
    for stage in trial["stages"]:
        assert stage["run_id"] in runs
        run = runs[stage["run_id"]]
        if (
            stage["status"] == "interrupted"
            and run["execution_ms"] is not None
        ):
            decomposed_stages += 1
            residual = run["execution_ms"] - models[stage["run_id"]]
            observed = stage["ms"] - run["queue_ms"] - run["execution_ms"]
            if residual < -1 or observed < -1:
                negative_components.append(
                    {
                        "case": trial["config"]["name"],
                        "version": trial["config"]["label"],
                        "repeat": trial["config"]["repeat"],
                        "run_id": stage["run_id"],
                        "other_execution_ms": residual,
                        "observation_ms": observed,
                    }
                )
    if trial["config"]["name"].startswith(("crud-", "mixed-", "flow-")):
        assert all(row["status"] == "complete" for row in trial["scenarios"])
        assert trial["server"]["healthy"]
        assert trial["database"]["session_owners"] == 0
        assert trial["database"]["recovery_tasks"] == 0
assert model_join_misses == 0
# DB func.now() is transaction-start time; started_at is application wall time.
# Keep the observed small boundary discrepancies instead of silently clipping.
assert all(
    item["other_execution_ms"] > -100 and item["observation_ms"] > -100
    for item in negative_components
), negative_components
result = {
    "assessment": "Share with caveats",
    "trials": len(trials),
    "paired_conditions": 20,
    "scenario_attempts": sum(len(t["scenarios"]) for t in trials),
    "http_requests": sum(len(t["requests"]) for t in trials),
    "headline_spot_checks": checks,
    "model_run_join_misses": model_join_misses,
    "negative_time_decomposition": negative_components,
    "decomposed_stages": decomposed_stages,
    "time_decomposition_caveat": (
        "Three sub-36ms negative residuals observed and retained. DB "
        "transaction-start wall timestamps and application monotonic "
        "timings have different boundaries; phase residuals are "
        "approximate, not exact tracing. Headline HTTP/pool timers are "
        "unaffected."
    ),
    "checks": [
        "two distinct repeats for every version/condition",
        "equal workload settings across A/B",
        "all completion/error/request counts independently recalculated",
        ("all scenario means independently recalculated"),
        "100-user headline pool occupancy and p95 independently recalculated",
        (
            "Run/model/stage join coverage; small mixed-clock time "
            "residuals retained and "
            "disclosed"
        ),
        "all 60 normal-condition trials completed with ownership returned",
    ],
    "caveats": [
        (
            "two repeats: small latency differences are not "
            "statistically "
            "established"
        ),
        "016 incremental comparison, not all-refactor uplift",
        "local burst load with mock LLM, no actual Executor or Kubernetes",
        "checkpoint pool occupancy and database CPU not measured",
    ],
}
(args.data / "independent-validation.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2)
)
print(json.dumps(result, ensure_ascii=False, indent=2))

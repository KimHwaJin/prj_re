"""Compare polling trials, keeping cleanup stalls and healthy-worker windows distinct."""

import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import statistics

from analyze_diagnostics import PHASES, pctl


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def distribution(values):
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "p50": pctl(values, 0.5),
        "p95": pctl(values, 0.95),
        "p99": pctl(values, 0.99),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def lines(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root
    traces = [r for f in (root / "traces").glob("*.jsonl") for r in lines(f)]
    ends = {r["run_id"]: r for r in traces if r["event"] == "run_end"}
    starts = {r["run_id"]: r for r in traces if r["event"] == "run_start"}
    observations = lines(root / "journeys.jsonl.runs.jsonl")
    observed = {r["id"]: r for r in observations if r["terminal"]}
    journeys = lines(root / "journeys.jsonl")
    snapshots = [r for r in traces if r["event"] == "stall_snapshot"]
    result = {
        "scope": (
            "100 concurrent closed-loop users; LLM mock 0 ms; four Run "
            "workers; no Executor submission. Polling changes both GET "
            "load and timing of subsequent POST arrivals. Stalls "
            "excluded only from explicitly labeled healthy segments, "
            "never from whole-trial "
            "failures."
        ),
        "trials": [],
    }
    for folder in sorted(root.glob("poll-*")):
        if not (folder / "database-runs-before-cleanup.json").exists():
            continue
        meta = json.loads((folder / "metadata.json").read_text())
        stage = json.loads((folder / "stage-100.json").read_text())
        db = json.loads(
            (folder / "database-runs-before-cleanup.json").read_text()
        )
        ids = {r["run_id"] for r in db}
        start, end = ts(stage["started_at"]), ts(stage["finished_at"])
        resources = lines(folder / "resources.jsonl")
        stalled = {}
        for r in snapshots:
            if r["run_id"] not in ids:
                continue
            owner = next((t for t in r["tasks"] if t["owner"]), {})
            frames = [
                f
                for f in owner.get("stack", [])
                if f["function"] == "_run_cancellable"
            ]
            if not any(f["line"] == 104 for f in frames):
                continue
            item = stalled.setdefault(
                r["run_id"],
                {
                    "pid": r["pid"],
                    "started_at": starts[r["run_id"]]["at"],
                    "first_snapshot": r["at"],
                    "snapshots": 0,
                    "owner_frames": frames,
                    "ended_at": ends.get(r["run_id"], {}).get("at"),
                },
            )
            item["snapshots"] += 1
            item["last_snapshot"] = r["at"]

        def analyze_window(a, b, label):
            # interrupted Runs have completed_at=NULL; the diagnostic end marks worker return after persistence.
            completed = [
                r
                for r in db
                if r["status"] == "interrupted"
                and r["run_id"] in ends
                and a <= ts(ends[r["run_id"]]["at"]) < b
            ]
            normal = [
                ends[r["run_id"]]
                for r in completed
                if r["run_id"] in ends and r["run_id"] not in stalled
            ]
            queue = [
                (ts(r["started_at"]) - ts(r["created_at"])) * 1000
                for r in completed
                if r["started_at"]
            ]
            detection, business_detection = [], []
            for r in completed:
                obs, trace = observed.get(r["run_id"]), ends.get(r["run_id"])
                if obs:
                    business_detection.append(
                        (ts(obs["observed_at"]) - ts(r["updated_at"])) * 1000
                    )
                    if trace:
                        detection.append(
                            (ts(obs["observed_at"]) - ts(trace["at"])) * 1000
                        )
            js = [
                j
                for j in journeys
                if meta["poll_seconds"] == j["poll_seconds"]
                and a <= ts(j["finished_at"]) < b
            ]
            rs = [r for r in resources if a <= ts(r["at"]) < b]
            phases = {
                name: distribution(
                    [
                        r["timings"].get(name, {}).get("total_ms", 0)
                        for r in normal
                    ]
                )
                for name in PHASES
            }
            residual = [
                r["elapsed_ms"]
                - sum(
                    r["timings"].get(k, {}).get("total_ms", 0) for k in PHASES
                )
                for r in normal
            ]
            assert all(v > -1 for v in residual), (
                "Phase overlap invalidates residual"
            )
            phases["other_worker_time"] = distribution(residual)
            nested_names = sorted(
                {
                    k
                    for r in normal
                    for k in r["timings"]
                    if k.startswith(
                        (
                            "sql.",
                            "checkpoint.",
                            "checkpoint_pool.getconn",
                            "bridge_pool.getconn",
                        )
                    )
                }
            )
            row = {
                "label": label,
                "start_epoch": a,
                "end_epoch": b,
                "seconds": b - a,
                "db_run_arrivals": sum(
                    a <= ts(r["created_at"]) < b for r in db
                ),
                "db_run_completions": len(completed),
                "db_run_completions_per_second": len(completed) / (b - a),
                "successful_journeys": sum(j["ok"] for j in js),
                "failed_journeys": sum(not j["ok"] for j in js),
                "journey_success_per_second": sum(j["ok"] for j in js)
                / (b - a),
                "journey_ms": distribution(
                    [j["elapsed_ms"] for j in js if j["ok"]]
                ),
                "queue_ms": distribution(queue),
                "worker_ms": distribution([r["elapsed_ms"] for r in normal]),
                "detection_after_worker_end_ms": distribution(detection),
                "detection_after_db_updated_at_ms": distribution(
                    business_detection
                ),
                "completion_detection_lower_bound_ms": distribution(
                    [max(0, v) for v in detection]
                ),
                "completion_detection_upper_bound_ms": distribution(
                    business_detection
                ),
                "negative_detection_count": sum(v < 0 for v in detection),
                "loop_lag_max_per_run_ms": distribution(
                    [r["loop_lag_max_ms"] for r in normal]
                ),
                "phase_ms": phases,
                "resource_samples": len(rs),
                "cpu_percent": {},
                "nested_timings": {
                    name: {
                        "total_ms_per_run": distribution(
                            [
                                r["timings"].get(name, {}).get("total_ms", 0)
                                for r in normal
                            ]
                        ),
                        "calls_per_run": distribution(
                            [
                                r["timings"].get(name, {}).get("count", 0)
                                for r in normal
                            ]
                        ),
                    }
                    for name in nested_names
                },
                "pending": distribution(
                    [r["runs"]["pending"] for r in rs if "runs" in r]
                ),
                "db_active": distribution(
                    [r["database"]["active"] for r in rs if "database" in r]
                ),
                "db_lock_waits": distribution(
                    [
                        r["database"]["lock_waits"]
                        for r in rs
                        if "database" in r
                    ]
                ),
                "completed_run_stage_counts": dict(
                    Counter(
                        observed.get(r["run_id"], {}).get(
                            "stage", "unobserved"
                        )
                        for r in completed
                    )
                ),
            }
            row["client_post_arrival_buckets"] = {}
            submissions = [
                ts(r["submitted_at"])
                for r in observations
                if r["id"] in ids and a <= ts(r["submitted_at"]) < b
            ]
            for width in (0.1, 1.0):
                counts = Counter(int((t - a) / width) for t in submissions)
                bins = [
                    counts.get(i, 0)
                    for i in range(math.floor((b - a) / width))
                ]
                avg = statistics.mean(bins) if bins else 0
                row["client_post_arrival_buckets"][str(width)] = {
                    **distribution(bins),
                    "cv": statistics.pstdev(bins) / avg if avg else None,
                }
            for name in ("api", "postgres", "locust"):
                row["cpu_percent"][name] = distribution(
                    [
                        float(c["CPUPerc"].rstrip("%"))
                        for r in rs
                        for c in r.get("containers", [])
                        if c["Name"] == f"dtest-agent-loadtest-{name}-1"
                    ]
                )
            # Sample buckets wholly inside a window: no interpolation across a stall boundary.
            previous = None
            counts, bucket_seconds = Counter(), 0
            for sample in stage["samples"]:
                if (
                    previous is not None
                    and a <= ts(previous["at"]) < ts(sample["at"]) <= b
                ):
                    old = {
                        (s["method"], s["name"]): s["num_requests"]
                        for s in previous["data"]["stats"]
                    }
                    for s in sample["data"]["stats"]:
                        key = (s["method"], s["name"])
                        if key[0] in ("GET", "POST", "PATCH", "DELETE"):
                            counts[key] += s["num_requests"] - old.get(key, 0)
                    bucket_seconds += ts(sample["at"]) - ts(previous["at"])
                previous = sample
            row["http_sample_window_seconds"] = bucket_seconds
            row["http_sample_rates"] = [
                {
                    "method": k[0],
                    "name": k[1],
                    "requests": v,
                    "rps": v / bucket_seconds if bucket_seconds else None,
                }
                for k, v in counts.items()
            ]
            return row

        whole = analyze_window(
            start, end, "entire measurement including stalled-worker periods"
        )
        http = [
            s
            for s in stage["stats"]["stats"]
            if s["method"] in ("GET", "POST", "PATCH", "DELETE")
        ]
        whole["http"] = [
            {
                k: s[k]
                for k in (
                    "method",
                    "name",
                    "num_requests",
                    "num_failures",
                    "avg_response_time",
                    "response_time_percentile_0.95",
                )
            }
            | {"rps": s["num_requests"] / stage["elapsed_seconds"]}
            for s in http
        ]
        whole["flow"] = next(
            s
            for s in stage["stats"]["stats"]
            if s["name"] == "SCENARIO/approval_wait"
        )
        boundaries = sorted(
            {
                start,
                end,
                *[
                    max(start, min(end, ts(s["started_at"])))
                    for s in stalled.values()
                ],
                *[
                    max(start, min(end, ts(s["ended_at"])))
                    for s in stalled.values()
                    if s["ended_at"]
                ],
            }
        )
        windows = []
        for a, b in zip(boundaries, boundaries[1:]):
            if b <= a:
                continue
            unavailable = sum(
                ts(s["started_at"])
                <= a
                < (ts(s["ended_at"]) if s["ended_at"] else float("inf"))
                for s in stalled.values()
            )
            window = analyze_window(
                a, b, f"{4 - unavailable} available Run workers"
            )
            window["available_workers"] = 4 - unavailable
            windows.append(window)
        trial_observed = [r for r in observations if r["id"] in ids]
        trial_journeys = [
            j
            for j in journeys
            if ts(meta["started_at"])
            <= ts(j["finished_at"])
            <= ts(meta["finished_at"])
        ]
        assert {r["poll_seconds"] for r in trial_observed} == {
            meta["poll_seconds"]
        }, "Polling option not applied consistently"
        assert len({r["id"] for r in trial_observed}) == len(trial_observed), (
            "Duplicate Run observation"
        )
        matched = (
            analyze_window(
                start,
                start + 200,
                "common first 200 seconds, all four workers healthy",
            )
            if windows[0]["available_workers"] == 4
            and windows[0]["seconds"] >= 200
            else None
        )
        result["trials"].append(
            {
                "trial": folder.name,
                "poll_seconds": meta["poll_seconds"],
                "whole": whole,
                "matched_first_200_seconds": matched,
                "windows": windows,
                "cleanup_stalls": stalled,
                "cohort": {
                    "created_runs": len(db),
                    "status_before_cleanup": dict(
                        Counter(r["status"] for r in db)
                    ),
                    "retried": sum(r["attempt_count"] > 1 for r in db),
                    "observations": len(trial_observed),
                    "missing_observation_ids": sorted(
                        ids - {r["id"] for r in trial_observed}
                    ),
                    "missing_trace_start_ids": sorted(ids - starts.keys()),
                    "missing_trace_end_ids": sorted(ids - ends.keys()),
                    "nonterminal_observations": sum(
                        not r["terminal"] for r in trial_observed
                    ),
                    "successful_journeys_including_warmup_and_drain": sum(
                        j["ok"] for j in trial_journeys
                    ),
                    "failed_journeys_including_warmup_and_drain": sum(
                        not j["ok"] for j in trial_journeys
                    ),
                    "journey_errors": [
                        j["error"] for j in trial_journeys if not j["ok"]
                    ],
                },
                "executor_before": meta["before_mock_executor"],
                "executor_after": meta["after_mock_executor"],
            }
        )
    (root / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            [
                {
                    "trial": t["trial"],
                    "poll_seconds": t["poll_seconds"],
                    "stalls": len(t["cleanup_stalls"]),
                    "journeys_per_s": t["whole"]["journey_success_per_second"],
                    "queue_ms": t["whole"]["queue_ms"],
                    "worker_ms": t["whole"]["worker_ms"],
                    "detection_ms": t["whole"][
                        "detection_after_worker_end_ms"
                    ],
                    "windows": [
                        {
                            "workers": w["available_workers"],
                            "seconds": w["seconds"],
                        }
                        for w in t["windows"]
                    ],
                }
                for t in result["trials"]
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

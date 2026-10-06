"""Aggregate diagnostic evidence without adding nested span durations together."""

import argparse
from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import statistics

PHASES = [
    "runtime.dependencies",
    "runtime.bridge_open",
    "runtime.checkpointer_open",
    "runtime.graph_build",
    "graph.invoke",
    "persistence.link_task",
    "persistence.state_delta",
    "checkpoint_pool.close",
    "bridge_pool.close",
    "run.token_events_close",
    "run.lock_final_rows",
    "run.final_commit",
]


def stamp(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def mean(a):
    return statistics.mean(a) if a else None


def pctl(a, q):
    return sorted(a)[math.ceil(len(a) * q) - 1] if a else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root
    rows = [
        json.loads(line)
        for f in (root / "traces").glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    journeys = [
        json.loads(line)
        for line in (root / "journeys.jsonl").read_text().splitlines()
    ]
    start_rows = [r for r in rows if r["event"] == "run_start"]
    end_rows = [r for r in rows if r["event"] == "run_end"]
    # A retry may execute the same Run ID again; keep every attempt for timings.
    starts = {
        r["run_id"]: r for r in sorted(start_rows, key=lambda r: r["at"])
    }
    ends = {r["run_id"]: r for r in sorted(end_rows, key=lambda r: r["at"])}
    snapshots = [r for r in rows if r["event"] == "stall_snapshot"]
    summary = {
        "stages": [],
        "trace_counts": {
            "starts": len(start_rows),
            "ends": len(end_rows),
            "snapshots": len(snapshots),
        },
        "unfinished_run_ids": sorted(starts.keys() - ends.keys()),
        "journeys_all": {
            "success": sum(j["ok"] for j in journeys),
            "failed": sum(not j["ok"] for j in journeys),
        },
        "stalled_runs": {},
        "scope": (
            "Trace timings grouped by Run diagnostic completion time "
            "within measurement windows. Nested checkpoint/SQL timings "
            "are separate and must not be added to disjoint phases. "
            "Journey totals include "
            "warmup/drain."
        ),
    }
    for s in snapshots:
        info = summary["stalled_runs"].setdefault(
            s["run_id"],
            {"pid": s["pid"], "first_snapshot": s["at"], "snapshot_count": 0},
        )
        info.update(
            last_snapshot=s["at"],
            last_active=s["active"],
            last_pools=s["pools"],
        )
        info["snapshot_count"] += 1
    for folder in sorted(root.glob("repeat-*")):
        if not (folder / "stages.json").exists():
            continue
        for stage in json.loads((folder / "stages.json").read_text()):
            start, end = (
                stamp(stage["started_at"]),
                stamp(stage["finished_at"]),
            )
            group = [r for r in end_rows if start <= stamp(r["at"]) <= end]
            good = [r for r in group if r["outcome"] == "returned"]
            http = [
                r
                for r in stage["stats"]["stats"]
                if r["method"] in ("GET", "POST", "PATCH", "DELETE")
            ]
            flow = next(
                (
                    r
                    for r in stage["stats"]["stats"]
                    if r["name"] == "SCENARIO/approval_wait"
                ),
                {},
            )
            run_dates = [
                r
                for j in journeys
                if j["ok"]
                for r in j["runs"]
                if start <= stamp(r["updated_at"]) <= end
            ]
            queue = [
                (
                    stamp(r["started_at"]) - stamp(r["created_at"])
                ).total_seconds()
                * 1000
                for r in run_dates
            ]
            total = [r["elapsed_ms"] for r in good]
            occupied = sum(
                max(
                    0,
                    (
                        min(end, stamp(r["at"]))
                        - max(start, stamp(r["started_at"]))
                    ).total_seconds(),
                )
                for r in end_rows
            )
            gaps = []
            for pid in {r["pid"] for r in end_rows}:
                ordered = sorted(
                    [r for r in end_rows if r["pid"] == pid],
                    key=lambda r: r["started_at"],
                )
                for prev, current in zip(ordered, ordered[1:]):
                    if (
                        start
                        <= stamp(prev["at"])
                        <= stamp(current["started_at"])
                        <= end
                    ):
                        gaps.append(
                            (
                                stamp(current["started_at"])
                                - stamp(prev["at"])
                            ).total_seconds()
                            * 1000
                        )
            components = []
            for name in PHASES:
                vals = [
                    r["timings"].get(name, {}).get("total_ms", 0) for r in good
                ]
                components.append(
                    {
                        "phase": name,
                        "mean_ms_per_run": mean(vals),
                        "p95_ms_per_run": pctl(vals, 0.95),
                    }
                )
            residual = [
                r["elapsed_ms"]
                - sum(
                    r["timings"].get(k, {}).get("total_ms", 0) for k in PHASES
                )
                for r in good
            ]
            assert all(v >= -1 for v in residual), (
                "Disjoint phase assumption violated"
            )
            components.append(
                {
                    "phase": "other_worker_time",
                    "mean_ms_per_run": mean(residual),
                    "p95_ms_per_run": pctl(residual, 0.95),
                }
            )
            nested = defaultdict(list)
            for r in good:
                for name, value in r["timings"].items():
                    if name.startswith(
                        (
                            "checkpoint.",
                            "checkpoint_pool.getconn",
                            "chain.",
                            "sql.",
                        )
                    ):
                        nested[name].append(value)
            summary["stages"].append(
                {
                    "repeat": folder.name,
                    "users": stage["users"],
                    "seconds": stage["elapsed_seconds"],
                    "http_requests": sum(r["num_requests"] for r in http),
                    "http_failures": sum(r["num_failures"] for r in http),
                    "journeys": flow.get("num_requests", 0),
                    "journey_failures": flow.get("num_failures", 0),
                    "journeys_per_sec": (
                        flow.get("num_requests", 0)
                        - flow.get("num_failures", 0)
                    )
                    / stage["elapsed_seconds"],
                    "journey_p95_ms": flow.get(
                        "response_time_percentile_0.95"
                    ),
                    "journey_p99_ms": flow.get(
                        "response_time_percentile_0.99"
                    ),
                    "completed_traces": len(group),
                    "trace_error_count": len(group) - len(good),
                    "run_mean_ms": mean(total),
                    "run_p95_ms": pctl(total, 0.95),
                    "run_p99_ms": pctl(total, 0.99),
                    "queue_mean_ms": mean(queue),
                    "queue_p95_ms": pctl(queue, 0.95),
                    "completed_run_slot_occupancy_pct": occupied
                    / (stage["elapsed_seconds"] * 4)
                    * 100,
                    "between_runs_gap_mean_ms": mean(gaps),
                    "between_runs_gap_p95_ms": pctl(gaps, 0.95),
                    "event_loop_lag_p95_ms": pctl(
                        [r["loop_lag_max_ms"] for r in good], 0.95
                    ),
                    "phase_breakdown": components,
                    "nested_timings": [
                        {
                            "name": k,
                            "runs_with_metric": len(v),
                            "calls_per_run": sum(i["count"] for i in v)
                            / len(good),
                            "mean_ms_per_run": sum(i["total_ms"] for i in v)
                            / len(good),
                            "max_call_ms": max(i["max_ms"] for i in v),
                        }
                        for k, v in sorted(nested.items())
                    ],
                }
            )
    (root / "analysis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                **summary,
                "stages": [
                    {
                        k: v
                        for k, v in s.items()
                        if k not in ("phase_breakdown", "nested_timings")
                    }
                    for s in summary["stages"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

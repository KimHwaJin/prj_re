"""Reconcile per-user wall time with per-attempt spans, without summing nested spans."""

import argparse, collections, gzip, json, math, statistics
from pathlib import Path


def summary(values):
    values = sorted(values)
    return {
        "mean": statistics.mean(values) if values else 0,
        "p50": statistics.median(values) if values else 0,
        "p95": values[min(len(values) - 1, math.ceil(0.95 * len(values)) - 1)]
        if values
        else 0,
        "max": max(values, default=0),
    }


def merge(intervals):
    result = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def length(intervals):
    return sum(b - a for a, b in merge(intervals))


def analyze(path):
    r = json.loads(
        gzip.decompress(path.read_bytes())
        if path.suffix == ".gz"
        else path.read_bytes()
    )
    n = r["config"]["users"]
    server = r["server"]
    db = r["database"]
    assert len(r["scenarios"]) == n and all(
        x["status"] == "complete" for x in r["scenarios"]
    )
    assert len(r["stages"]) == 4 * n and len(db["runs"]) == 4 * n
    assert len(r["llm"]["events"]) == 4 * n and all(
        x["outcome"] == "ok" and x["ms"] >= 4990 for x in r["llm"]["events"]
    )
    assert not r["stop_reason"] and server["healthy"] and not server["faults"]
    assert not db["recovery_tasks"] and not db["session_owners"]
    assert all(
        x["status"] == "interrupted" and x["attempt_count"] == 1
        for x in db["runs"]
    )
    capacity = r["config"].get("total_slots", 4)
    assert (
        server["peak_worker"] <= capacity and server["peak_graph"] <= capacity
    )
    assert all(not x["error"] for x in r["requests"])
    workers = {x["run_id"]: x for x in server["workers"]}
    runs = {x["run_id"]: x for x in db["runs"]}
    assert len(workers) == 4 * n
    assert len(server["models"]) == 4 * n and all(
        x["mode"] == "async" for x in server["models"]
    )
    assert all(
        x["outcome"] == "ok" for x in server["workers"] + server["graphs"]
    )
    assert all(x["kind"] != "run_get" for x in server["http"])
    assert all(
        not x["forced_kill"]
        for x in json.loads(
            (path.parent / "shutdown.json").read_text()
        ).values()
    )
    peruser = collections.defaultdict(lambda: collections.defaultdict(float))
    phases = []
    for stage in r["stages"]:
        rid = stage["attempt_id"]
        w = workers[rid]
        row = runs[rid]
        u = peruser[stage["user"]]
        selected = [x for x in server["spans"] if x["run_id"] == rid]
        intervals = lambda spans: [
            (max(w["start"], x["start"]), min(w["end"], x["end"]))
            for x in spans
        ]
        model_intervals = intervals(
            x for x in server["models"] if x["run_id"] == rid
        )
        projection_intervals = intervals(
            x for x in selected if x["name"].startswith("persistence.")
        )
        init_intervals = intervals(
            x for x in selected if x["name"].startswith("runtime.")
        )
        checkpoint_intervals = intervals(
            x for x in selected if x["name"].startswith("checkpoint.")
        )
        # Priority partitions: model, projection, init, checkpoint exclusive, remainder.
        accumulated = []
        parts = {}
        for name, ints in [
            ("llm_s", model_intervals),
            ("projection_s", projection_intervals),
            ("init_s", init_intervals),
            ("checkpoint_exclusive_s", checkpoint_intervals),
        ]:
            parts[name] = length(accumulated + ints) - length(accumulated)
            accumulated += ints
        parts["other_internal_s"] = (w["end"] - w["start"]) - length(
            accumulated
        )
        assert parts["other_internal_s"] >= -1e-5
        parts["worker_s"] = w["ms"] / 1000
        parts["queue_s"] = row["queue_ms"] / 1000
        parts["stage_s"] = stage["ms"] / 1000
        assert (
            abs(
                sum(
                    parts[k]
                    for k in [
                        "llm_s",
                        "projection_s",
                        "init_s",
                        "checkpoint_exclusive_s",
                        "other_internal_s",
                    ]
                )
                - parts["worker_s"]
            )
            < 0.001
        )
        posts = [
            q
            for q in r["requests"]
            if q["user"] == stage["user"] and q["kind"] == "run_post"
        ]
        user_stages = [s for s in r["stages"] if s["user"] == stage["user"]]
        post = posts[user_stages.index(stage)]
        # accepted_at is sampled just after the request helper returns. The helper
        # duration reconstructs stage start within local Python call overhead.
        observed_perf = (
            stage["accepted_at"] - post["ms"] / 1000 + stage["ms"] / 1000
        )
        commits = [x for x in selected if x["name"] == "run.final_commit"]
        assert len(commits) == 1
        parts["delivery_s"] = observed_perf - commits[0]["end"]
        parts["admission_s"] = post["ms"] / 1000
        parts["model_calls"] = len(model_intervals)
        assert (
            parts["model_calls"]
            == {
                "data_selection": 2,
                "analysis_context": 0,
                "workflow_candidate_selection": 2,
                "workflow_approval": 0,
            }[stage["stage"]]
        )
        for k, v in parts.items():
            u[k] += v
        phases.append(
            {"user": stage["user"], "stage": stage["stage"], **parts}
        )
    for scenario in r["scenarios"]:
        u = peruser[scenario["user"]]
        u["total_s"] = scenario["ms"] / 1000
        u["internal_s"] = u["worker_s"] - u["llm_s"]
        u["outside_worker_queue_s"] = (
            u["total_s"] - u["worker_s"] - u["queue_s"]
        )
        # Total decomposition exact; outside includes session/admission/think/SSE,
        # and tiny timestamp/claim cleanup boundary differences. It is NOT all API CPU.
    actor = collections.defaultdict(lambda: {"count": 0, "seconds": 0})
    fingerprints = collections.defaultdict(lambda: {"count": 0, "seconds": 0})
    for q in server["sql"]:
        for target in (actor[q["kind"]], fingerprints[q["fingerprint"]]):
            target["count"] += 1
            target["seconds"] += q["ms"] / 1000
    result = {
        "users": n,
        "source_commit": r["config"]["commit"],
        "elapsed_s": r["elapsed_s"],
        "complete_users": n,
        "attempts": len(workers),
        "model_calls": len(r["llm"]["events"]),
        "stream_model_calls": sum(x["stream"] for x in r["llm"]["events"]),
        "per_user": {
            k: summary([u[k] for u in peruser.values()])
            for k in next(iter(peruser.values()))
        },
        "stages": {
            name: {
                k: summary([x[k] for x in phases if x["stage"] == name])
                for k in parts
            }
            for name in dict.fromkeys(x["stage"] for x in phases)
        },
        "sql_by_actor": dict(actor),
        "sql_total": sum(x["count"] for x in actor.values()),
        "top_sql": sorted(
            [{"fingerprint": k, **v} for k, v in fingerprints.items()],
            key=lambda x: -x["seconds"],
        )[:20],
        "pool_acquire_ms": summary([x["ms"] for x in server["acquires"]]),
        "event_loop_lag_ms": summary([x["lag_ms"] for x in server["samples"]]),
        "peak_worker": server["peak_worker"],
        "peak_graph": server["peak_graph"],
        "slot_occupancy": sum(x["ms"] / 1000 for x in server["workers"])
        / (capacity * r["elapsed_s"]),
        "commit_calls_by_actor": dict(
            collections.Counter(x["kind"] for x in server["commits"])
        ),
        "pool_wait_over_100ms": {
            "total": sum(x["ms"] > 100 for x in server["acquires"]),
            "first_2s": sum(
                x["ms"] > 100 and x["at"] - server["start"] < 2
                for x in server["acquires"]
            ),
        },
        "peak_pool_checked_out": max(
            x["checked_out"] for x in server["samples"]
        ),
        "cpu_seconds": server["cpu_seconds"],
        "rss_peak_bytes": server["rss_peak_bytes"],
        "resource_events": server["resources"],
        "raw": path.parent.name + "/raw.json.gz",
        "checks": (
            "all users, models, attempts, health, session release, and "
            "time partitions "
            "verified"
        ),
    }
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    paths = list(a.folder.glob("flow-*/raw.json")) or list(
        a.folder.glob("flow-*/raw.json.gz")
    )
    results = [analyze(f) for f in paths]
    results.sort(key=lambda r: r["users"])
    a.output.write_text(json.dumps(results, indent=2) + "\n")
    for r in results:
        print(
            r["users"],
            {
                k: round(r["per_user"][k]["mean"], 3)
                for k in (
                    "total_s",
                    "queue_s",
                    "llm_s",
                    "internal_s",
                    "projection_s",
                    "delivery_s",
                )
            },
            "SQL",
            r["sql_total"],
        )

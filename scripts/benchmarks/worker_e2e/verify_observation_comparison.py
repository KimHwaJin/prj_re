"""Independent raw-to-results recomputation; deliberately no analyzer imports."""

from collections import Counter, defaultdict
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics


def percentile(values):
    values = sorted(values)
    rank = (len(values) - 1) * 0.95
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return (
        values[lo] * (hi - rank) + values[hi] * (rank - lo)
        if lo != hi
        else values[lo]
    )


def derive(raw):
    cfg = raw["config"]
    n = cfg["users"]
    server = raw["server"]
    database = raw["database"]
    large = cfg["observation_profile"] == "large20"
    operations = 20 if large else 2
    reviews = 20 if large else 1
    step_ids = (
        ["load", *(f"profile_{i:02d}" for i in range(1, 20))]
        if large
        else ["load", "profile", "statistics", "outliers"]
    )
    assert raw["passed"] and raw["errors"] == [] and len(raw["results"]) == n
    assert (
        raw["elapsed_seconds"] > 0
        and cfg["delay_ms"] == 0
        and cfg["checkpoint_pool"] == 4
        and cfg["total_capacity"] == 20
    )
    assert (
        server["peak_shared"] <= 20
        and server["current_shared"]
        == server["crud_connections_checked_out"]
        == 0
    )
    assert (
        database["session_owners"]
        == database["recovery_tasks"]
        == database["inbox_pending"]
        == 0
    )
    assert database["outbox_pending"] == 0 or (
        database["outbox_pending"] is None
        and database.get("outbox_retired") is True
    )
    assert (
        len(database["runs"]) == 3 * n
        and len({r["run_id"] for r in database["runs"]}) == 3 * n
    )
    assert Counter(r["status"] for r in database["runs"]) == {
        "interrupted": n * 2,
        "success": n,
    }
    assert len(database["common_commands"]) == n * (operations + 4)
    assert all(c["state"] == "DONE" for c in database["common_commands"])
    assert Counter(m["role"] for m in server["models"]) == {
        "planning_select": n,
        "planning_plan": n,
        "review": reviews * n,
        "report": n,
    }
    handlers = [h for h in server["event_handlers"] if not h["error"]]
    assert (
        len(handlers)
        == len({h["event_id"] for h in handlers})
        == n * (operations + 1)
    )
    assert all(
        h["error"] in (None, "DeferEvent", "_HandoffPending")
        for h in server["event_handlers"]
    )
    assert all(h["status"] < 400 for h in server["http"])
    assert not raw["mock"]["tasks_failed"] and raw["mock"]["pending"] == 0
    executions = {r["execution_id"] for r in raw["results"]}
    assert len(executions) == n
    assert {
        e["execution_id"]
        for e in raw["mock"]["executions"]
        if e["execution_id"] in executions
    } == executions
    assert all(
        e["operations"] == operations and e["status"] == "SUCCEEDED"
        for e in raw["mock"]["executions"]
        if e["execution_id"] in executions
    )
    for r in raw["results"]:
        assert r["passed"] and r["report"]["status"] == "ready"
        assert [o["step_id"] for o in r["observations"]] == step_ids
        assert all(
            o["status"] == "SUCCEEDED"
            and not o["incomplete"]
            and o["summary"]["type"] == "service_fixture"
            for o in r["observations"]
        )
    cp = raw["checkpoint_profile"]
    calls = server["checkpoint_calls"]
    assert all(c["error"] is None for c in calls)
    assert {c["thread_id"] for c in calls} == {
        c["thread_id"] for c in cp["checkpoints"]
    }
    assert len({c["thread_id"] for c in calls}) == n
    assert len(cp["checkpoints"]) == sum(c["method"] == "aput" for c in calls)
    total = sum(
        c["checkpoint_json_bytes"] + c["metadata_json_bytes"]
        for c in cp["checkpoints"]
    )
    total += sum(
        c["bytes"]
        for collection in ("blobs", "writes")
        for c in cp[collection]
    )
    writes = sum(
        c["bytes"] for c in cp["writes"] if c["channel"] == "observations"
    )
    outer = sum(
        t["timings"].get("checkpoint." + method, {}).get("total_ms", 0)
        for t in server["traces"]
        for method in ("aput", "aput_writes")
    )
    serialization = sum(s["ms"] for c in calls for s in c["serialization"])
    timestamps = {
        e["event_id"]: e["at"]
        for e in raw["mock"]["timeline"]
        if e["stage"] == "event_published"
    }
    event_wait = [
        (h["start"] - timestamps[h["event_id"]]) * 1000 for h in handlers
    ]
    workers = {w["run_id"] for w in server["workers"]}
    queue = [r["queue_ms"] for r in database["runs"] if r["run_id"] in workers]
    seconds = [r["seconds"] for r in raw["results"]]
    semantics = hashlib.sha256(
        json.dumps(
            raw["results"][0]["observations"],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert all(
        hashlib.sha256(
            json.dumps(
                r["observations"], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        == semantics
        for r in raw["results"]
    )
    return {
        "mean_seconds": sum(seconds) / n,
        "p95_seconds": percentile(seconds),
        "makespan_seconds": raw["elapsed_seconds"],
        "batch_users_per_second": n / raw["elapsed_seconds"],
        "user_queue_mean_ms": sum(queue) / len(queue),
        "event_queue_mean_ms": sum(event_wait) / len(event_wait),
        "api_cpu_per_user_seconds": server["cpu_seconds"] / n,
        "crud_sql_per_user": sum(q["count"] for q in server["sql"]) / n,
        "saver_write_outer_ms_per_user": outer / n,
        "serialization_ms_per_user": serialization / n,
        "logical_mib_per_user": total / n / 1048576,
        "saver_read_ms_per_user": sum(
            (c["end"] - c["start"]) * 1000
            for c in calls
            if c["method"] == "aget_tuple"
        )
        / n,
        "column_payload_mib_per_user": (
            sum(
                c["checkpoint_storage_bytes"] + c["metadata_storage_bytes"]
                for c in cp["checkpoints"]
            )
            + sum(
                c["storage_bytes"] for k in ("blobs", "writes") for c in cp[k]
            )
        )
        / n
        / 1048576,
        "observation_write_mib_per_user": writes / n / 1048576,
        "checkpoints_per_user": len(cp["checkpoints"]) / n,
        "defer_attempts": len(server["event_handlers"]) - len(handlers),
        "history_reads": len(raw["mock"]["history_requests"]),
        "semantic_hash": semantics,
    }


def verify(folder):
    results = json.loads((folder / "results.json").read_text())
    summary = json.loads((folder / "summary.json").read_text())
    index = json.loads((folder / "raw-index.json").read_text())
    look = {
        (r["variant"], r["profile"], r["users"], r["repeat"]): r
        for r in results
    }
    assert len(look) == len(results) == len(index) == 24
    audit = json.loads((folder / "source-audit.json").read_text())
    checks = 0
    groups = defaultdict(list)
    semantics = defaultdict(set)
    for entry in index:
        payload = gzip.decompress((folder / entry["file"]).read_bytes())
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        raw = json.loads(payload)
        key = (
            entry["variant"],
            entry["profile"],
            entry["users"],
            entry["repeat"],
        )
        assert (
            raw["config"]["source_commit"]
            == entry["source_commit"]
            == look[key]["source_commit"]
            == audit["commits"][entry["variant"]]
        )
        assert (
            raw["config"]["source_sha256"]
            == audit["sources"][entry["variant"]]
        )
        values = derive(raw)
        semantics[entry["profile"]].add(values.pop("semantic_hash"))
        for field, value in values.items():
            assert math.isclose(
                look[key][field], value, rel_tol=1e-10, abs_tol=1e-8
            ), (key, field)
            checks += 1
        groups[key[:3]].append(look[key])
        checks += 1
    assert all(len(v) == 1 for v in semantics.values())
    summary_map = {
        (r["variant"], r["profile"], int(r["users"])): r for r in summary
    }
    assert set(summary_map) == set(groups)
    for key, rows in groups.items():
        assert sorted(r["repeat"] for r in rows) == (
            [1, 2, 3] if key[2] == 50 else [1]
        )
        target = summary_map[key]
        assert target["repeats"] == len(rows)
        for output, source in [
            ("mean_seconds", "mean_seconds"),
            ("makespan_seconds", "makespan_seconds"),
            ("batch_users_per_second", "batch_users_per_second"),
            ("logical_mib_per_user", "logical_mib_per_user"),
            ("saver_write_ms_per_user", "saver_write_outer_ms_per_user"),
            ("crud_sql_per_user", "crud_sql_per_user"),
            ("event_queue_mean_ms", "event_queue_mean_ms"),
            ("cpu_per_user_seconds", "api_cpu_per_user_seconds"),
        ]:
            assert math.isclose(
                target[output],
                statistics.mean(r[source] for r in rows),
                rel_tol=1e-10,
                abs_tol=1e-8,
            )
            checks += 1
        assert target["mean_min"] == min(
            r["mean_seconds"] for r in rows
        ) and target["mean_max"] == max(r["mean_seconds"] for r in rows)
    attempts = json.loads((folder / "attempts.json").read_text())
    completed = [a for a in attempts if a["exit_code"] == 0]
    failed = [a for a in attempts if a["exit_code"] != 0]
    assert len(completed) == 24 and {
        (a["variant"], a["profile"], a["users"], a["repeat"])
        for a in completed
    } == set(look)
    if failed:
        incident_index = json.loads(
            (folder / "incident-index.json").read_text()
        )
        assert incident_index
        for entry in incident_index:
            encoded = (folder / entry["file"]).read_bytes()
            payload = (
                encoded
                if entry["file"].endswith(".json.gz")
                else gzip.decompress(encoded)
            )
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
            checks += 1
        assert any(
            e["file"].endswith("failure-live.json.gz") for e in incident_index
        )
    excluded_index = (
        json.loads((folder / "excluded-index.json").read_text())
        if (folder / "excluded-index.json").exists()
        else []
    )
    for entry in excluded_index:
        payload = gzip.decompress((folder / entry["file"]).read_bytes())
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        checks += 1
    return {
        "assessment": (
            "Share with caveats: finite local zero-delay Worker burst, "
            "not SLA or HPA "
            "capacity"
        ),
        "trials": len(results),
        "user_scenarios": sum(r["users"] for r in results),
        "checks_passed": checks,
        "attempts": len(attempts),
        "failed_attempts": len(failed),
        "attempted_user_scenarios": sum(r["users"] for r in attempts),
        "adoption_blocked_by_unresolved_incident": bool(failed),
        "excluded_evidence_files_verified": len(excluded_index),
        "semantic_hashes_match": True,
        "repeat_coverage_verified": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    result = verify(args.folder)
    (args.folder / "validation.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))

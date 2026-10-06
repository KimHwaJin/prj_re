"""Validate complete multi-process trials before comparing latency and resources."""

import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path

PROFILE = Path(__file__).parents[1] / "runtime_profile/analyze.py"
spec = importlib.util.spec_from_file_location(
    "runtime_profile_analysis", PROFILE
)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def peak_overlap(intervals):
    level = peak = 0
    for _, delta in sorted(
        [(r["start"], 1) for r in intervals]
        + [(r["end"], -1) for r in intervals]
    ):
        level += delta
        peak = max(level, peak)
    return peak


def validate_processes(raw):
    cfg = raw["config"]
    rows = raw["per_process"]
    assert len(rows) == cfg["processes"] == len({r["pid"] for r in rows})
    assert len(raw["server"]["workers"]) == cfg["users"] * 4
    assert (
        len({w["run_id"] for w in raw["server"]["workers"]})
        == cfg["users"] * 4
    )
    assert all(
        r["peak_worker"] <= cfg["slots"] and r["peak_graph"] <= cfg["slots"]
        for r in rows
    )
    assert all(peak_overlap(r["workers"]) <= cfg["slots"] for r in rows)
    assert peak_overlap(raw["server"]["workers"]) <= cfg["total_slots"]
    assert all(r["healthy"] and not r["faults"] and r["samples"] for r in rows)
    assert raw["resource_samples"]
    for x in raw["resource_samples"]:
        assert (
            x["worker_rss_bytes"] > 0
            and x["db_connections"] >= cfg["processes"]
        )
        assert x["db_active"] <= x["db_connections"]
    for key in ("workers", "models", "sql", "graphs", "commits"):
        assert len(raw["server"][key]) == sum(len(r[key]) for r in rows)
    assert math.isclose(
        raw["server"]["cpu_seconds"], sum(r["cpu_seconds"] for r in rows)
    )


def analyze(path):
    raw = json.loads(
        gzip.decompress(path.read_bytes())
        if path.suffix == ".gz"
        else path.read_bytes()
    )
    validate_processes(raw)
    result = profile.analyze(path)
    cfg, samples = raw["config"], raw["resource_samples"]
    result.update(
        processes=cfg["processes"],
        concurrency=cfg["slots"],
        total_slots=cfg["total_slots"],
        repeat=cfg["repeat"],
        layout=f"{cfg['processes']}×{cfg['slots']}",
    )
    result["throughput_users_s"] = cfg["users"] / raw["elapsed_s"]
    result["worker_cpu_seconds_per_user"] = (
        result["cpu_seconds"] / cfg["users"]
    )
    result["worker_mean_cpu_cores"] = result["cpu_seconds"] / raw["elapsed_s"]
    result["simultaneous_peak_workers"] = peak_overlap(
        raw["server"]["workers"]
    )
    result["simultaneous_peak_graphs"] = peak_overlap(raw["server"]["graphs"])
    result["worker_rss_peak_mib"] = (
        max(s["worker_rss_bytes"] for s in samples) / 1024**2
    )
    result["api_rss_peak_mib"] = (
        max(s["worker_rss_bytes"] + s["supervisor_rss_bytes"] for s in samples)
        / 1024**2
    )
    result["worker_rss_first_mib"] = samples[0]["worker_rss_bytes"] / 1024**2
    result["supervisor_rss_peak_mib"] = (
        max(s["supervisor_rss_bytes"] for s in samples) / 1024**2
    )
    result["resource_sample_count"] = len(samples)
    result["sample_duration_ms"] = profile.summary(
        [x["sample_duration_s"] * 1000 for x in samples]
    )
    for key in (
        "db_connections",
        "db_active",
        "db_idle_in_transaction",
        "db_lock_waiters",
        "db_oldest_idle_transaction_s",
    ):
        result[key] = profile.summary([x[key] for x in samples])
    result["lock_wait_sample_fraction"] = sum(
        s["db_lock_waiters"] > 0 for s in samples
    ) / len(samples)
    result["sql_execution_ms"] = profile.summary(
        [x["ms"] for x in raw["server"]["sql"]]
    )
    result["workers_by_process"] = [
        len(r["workers"]) for r in raw["per_process"]
    ]
    result["process_metrics"] = [
        dict(
            pid=r["pid"],
            workers=len(r["workers"]),
            cpu_seconds=r["cpu_seconds"],
            peak_worker=r["peak_worker"],
            pool_acquire_ms=profile.summary([x["ms"] for x in r["acquires"]]),
            lag_ms=profile.summary([x["lag_ms"] for x in r["samples"]]),
        )
        for r in raw["per_process"]
    ]
    result["messages_per_user"] = raw["database"]["messages"] / cfg["users"]
    result["logs_per_user"] = raw["database"]["logs"] / cfg["users"]
    assert all(
        not r["forced_kill"] and r["returncode"] in (0, -15)
        for r in json.loads(
            (path.parent / "shutdown.json").read_text()
        ).values()
    )
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("folders", type=Path, nargs="+")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    rows, manifest = [], []
    for folder in a.folders:
        for path in sorted(folder.glob("flow-*/raw.json")):
            result = analyze(path)
            dest = a.output / "raw" / folder.name / path.parent.name
            dest.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            normalized = (
                data.decode()
                .replace(str(Path.home()), "<home>")
                .replace(str(folder), "<trial-root>")
                .encode()
            )
            (dest / "raw.json.gz").write_bytes(
                gzip.compress(normalized, mtime=0)
            )
            (dest / "shutdown.json").write_bytes(
                (path.parent / "shutdown.json").read_bytes()
            )
            result["raw"] = str((dest / "raw.json.gz").relative_to(a.output))
            rows.append(result)
            manifest.append(
                dict(
                    raw=result["raw"],
                    original_sha256=hashlib.sha256(data).hexdigest(),
                    normalized_sha256=hashlib.sha256(normalized).hexdigest(),
                )
            )
    assert rows
    assert len({(r["layout"], r["users"], r["repeat"]) for r in rows}) == len(
        rows
    )
    assert len({r["source_commit"] for r in rows}) == 1
    assert (
        len({r["messages_per_user"] for r in rows})
        == len({r["logs_per_user"] for r in rows})
        == 1
    )
    (a.output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    (a.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    for r in rows:
        print(
            r["layout"],
            r["users"],
            r["repeat"],
            round(r["per_user"]["total_s"]["mean"], 2),
            round(r["worker_rss_peak_mib"]),
            r["db_connections"]["max"],
        )


if __name__ == "__main__":
    main()

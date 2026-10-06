"""Validate every Worker cohort and export compressed evidence with source hashes."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import io

from analyze_checkpoint import analyze_checkpoint

SQL = """SELECT variant, profile, users, COUNT(*) AS repeats,
 AVG(mean_seconds) AS mean_seconds, MIN(mean_seconds) AS mean_min, MAX(mean_seconds) AS mean_max,
 AVG(p95_seconds) AS p95_trial_mean, MIN(p95_seconds) AS p95_min, MAX(p95_seconds) AS p95_max,
 AVG(makespan_seconds) AS makespan_seconds, AVG(batch_users_per_second) AS batch_users_per_second,
 AVG(user_queue_mean_ms) AS user_queue_mean_ms, AVG(event_queue_mean_ms) AS event_queue_mean_ms,
 AVG(api_cpu_per_user_seconds) AS cpu_per_user_seconds, AVG(crud_sql_per_user) AS crud_sql_per_user,
 AVG(saver_write_outer_ms_per_user) AS saver_write_ms_per_user,
 AVG(saver_read_ms_per_user) AS saver_read_ms_per_user, AVG(column_payload_mib_per_user) AS column_payload_mib_per_user,
 AVG(serialization_ms_per_user) AS serialization_ms_per_user,
 AVG(logical_mib_per_user) AS logical_mib_per_user, AVG(observation_write_mib_per_user) AS observation_write_mib_per_user,
 AVG(checkpoints_per_user) AS checkpoints_per_user, AVG(defer_attempts) AS defer_attempts
 FROM trials GROUP BY variant, profile, users ORDER BY profile, users, variant"""


def export(capture, output, supplements=()):
    assert not output.exists(), "Use fresh report folder"
    (output / "raw").mkdir(parents=True)
    roots = [capture, *supplements]
    receipts = []
    entries = []
    attempts = []
    incidents = []
    for root in roots:
        if (root / "runs.json").exists():
            source_receipt = json.loads((root / "runs.json").read_text())
            receipts.append({"capture": root.name, **source_receipt})
            for run in source_receipt["runs"]:
                attempts.append({"capture": root.name, **run})
                if run["exit_code"] != 0:
                    incidents.append((root / run["folder"], run))
                else:
                    entries.append((root / run["folder"], run))
        else:
            files = list(root.rglob("raw.json"))
            assert len(files) == 1, (
                "A diagnostic root needs one complete capture"
            )
            raw = json.loads(files[0].read_bytes())
            cfg = raw["config"]
            variant = {
                "5ca22a5846b187d2e6a48c2cab0582339a2442ea": "baseline",
                "63b2b6b3f6c271bcc599717cac190a7988935b81": "candidate",
            }[cfg["source_commit"]]
            run = {
                "variant": variant,
                "profile": cfg["observation_profile"],
                "users": cfg["users"],
                "repeat": cfg["repeat"],
                "folder": f"{variant}-{cfg['observation_profile']}-u{cfg['users']}-r{cfg['repeat']}",
                "exit_code": 0,
                "diagnostic_cause_logging": True,
            }
            attempts.append({"capture": root.name, **run})
            entries.append((root, run))
    receipt = {
        "runs": [run for _, run in entries],
        "capture_receipts": receipts,
    }
    rows, index = [], []
    digests = {}
    commits = {}
    for source_root, run in entries:
        files = list(source_root.rglob("raw.json"))
        assert len(files) == 1
        source = files[0]
        payload = source.read_bytes()
        raw = json.loads(payload)
        cfg = raw["config"]
        variant = run["variant"]
        assert (
            cfg["users"] == run["users"]
            and cfg["repeat"] == run["repeat"]
            and cfg["observation_profile"] == run["profile"]
        )
        assert (
            cfg["scenario"] == "executor"
            and cfg["architecture"] == "common"
            and not cfg["followup"]
        )
        assert (
            cfg["delay_ms"] == cfg["executor_delay_ms"] == 0
            and not cfg["real_executor"]
        )
        assert (
            cfg["total_capacity"],
            cfg["checkpoint_pool"],
            cfg["service_pool"],
            cfg["bridge_pool"],
        ) == (20, 4, 10, 4)
        assert (
            cfg["sse_seconds"],
            cfg["cancel_seconds"],
            cfg["claim_seconds"],
            cfg["notify"],
        ) == (0.5, 0.25, 0.25, "on")
        previous = digests.setdefault(variant, cfg["source_sha256"])
        assert previous == cfg["source_sha256"]
        assert (
            commits.setdefault(variant, cfg["source_commit"])
            == cfg["source_commit"]
        )
        result = analyze_checkpoint(raw)
        flow = result["flow"]
        n = cfg["users"]
        evidence = next(
            c for c in result["channels"] if c["channel"] == "observations"
        )
        row = {
            "variant": variant,
            "profile": run["profile"],
            "users": n,
            "repeat": cfg["repeat"],
            **{
                k: flow[k]
                for k in (
                    "mean_seconds",
                    "p95_seconds",
                    "makespan_seconds",
                    "batch_users_per_second",
                    "user_queue_mean_ms",
                    "api_cpu_per_user_seconds",
                    "crud_sql_per_user",
                    "event_defer_attempts",
                )
            },
            "event_queue_mean_ms": flow["event_publish_to_start_mean_ms"],
            "saver_write_outer_ms_per_user": result[
                "saver_write_outer_ms_per_user"
            ],
            "serialization_ms_per_user": result["serialization_ms_per_user"],
            "saver_read_ms_per_user": result["timings"]["aget_tuple"][
                "ms_per_user"
            ],
            "column_payload_mib_per_user": (
                sum(
                    c["checkpoint_storage_bytes"] + c["metadata_storage_bytes"]
                    for c in raw["checkpoint_profile"]["checkpoints"]
                )
                + sum(
                    c["storage_bytes"]
                    for k in ("blobs", "writes")
                    for c in raw["checkpoint_profile"][k]
                )
            )
            / n
            / 1048576,
            "history_reads": len(raw["mock"]["history_requests"]),
            "logical_mib_per_user": result["logical_payload_bytes_per_user"]
            / 1048576,
            "observation_write_mib_per_user": evidence["write_bytes"]
            / n
            / 1048576,
            "checkpoints_per_user": result["checkpoints_per_user"],
            "defer_attempts": flow["event_defer_attempts"],
            "source_commit": cfg["source_commit"],
        }
        rows.append(row)
        target = output / "raw" / (run["folder"] + ".json.gz")
        target.write_bytes(gzip.compress(payload, mtime=0))
        index.append(
            {
                "file": str(target.relative_to(output)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "uncompressed_bytes": len(payload),
                "source_commit": cfg["source_commit"],
                "variant": variant,
                "profile": run["profile"],
                "users": n,
                "repeat": cfg["repeat"],
            }
        )
        print("VALIDATED", run["folder"], flush=True)
    expected = {
        (v, p, n, r)
        for v in ("baseline", "candidate")
        for p in ("standard", "large20")
        for n in (1, 10, 30, 50)
        for r in ((1, 2, 3) if n == 50 else (1,))
    }
    assert (
        len(rows) == len(expected) == 24
        and {
            (r["variant"], r["profile"], r["users"], r["repeat"]) for r in rows
        }
        == expected
    )
    for variant, commit in commits.items():
        bundle_bytes = subprocess.check_output(
            ["git", "archive", commit, "src"]
        )
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes)) as bundle:
            hashes = {
                member.name: hashlib.sha256(
                    bundle.extractfile(member).read()
                ).hexdigest()
                for member in bundle.getmembers()
                if member.isfile() and member.name.endswith(".py")
            }
        assert hashes == digests[variant], (
            "Actual captured source differs from attributed commit"
        )
    runtime = lambda d: {
        p: h for p, h in d.items() if "/tests/" not in p and "/test/" not in p
    }
    before, after = runtime(digests["baseline"]), runtime(digests["candidate"])
    changed = {
        p
        for p in before.keys() | after.keys()
        if before.get(p) != after.get(p)
    }
    assert changed == {
        "src/dtest/agent_service/agents/analysis/state.py",
        "src/dtest/agent_service/agents/analysis/planning/graph.py",
        "src/dtest/agent_service/agents/analysis/execution/nodes.py",
        "src/dtest/agent_service/agents/analysis/execution/observation_state.py",
    }
    fields = [
        "variant",
        "profile",
        "users",
        "mean_seconds",
        "p95_seconds",
        "makespan_seconds",
        "batch_users_per_second",
        "user_queue_mean_ms",
        "event_queue_mean_ms",
        "api_cpu_per_user_seconds",
        "crud_sql_per_user",
        "saver_write_outer_ms_per_user",
        "saver_read_ms_per_user",
        "column_payload_mib_per_user",
        "serialization_ms_per_user",
        "logical_mib_per_user",
        "observation_write_mib_per_user",
        "checkpoints_per_user",
        "defer_attempts",
    ]
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE trials ("
            + ",".join(
                f + " " + ("TEXT" if f in ("variant", "profile") else "REAL")
                for f in fields
            )
            + ")"
        )
        db.executemany(
            "INSERT INTO trials VALUES ("
            + ",".join("?" for _ in fields)
            + ")",
            [[r[f] for f in fields] for r in rows],
        )
        summary = [dict(r) for r in db.execute(SQL)]
    for name, value in [
        ("results.json", rows),
        ("summary.json", summary),
        ("raw-index.json", index),
        ("runs.json", receipt),
        (
            "source-audit.json",
            {
                "commits": commits,
                "sources": digests,
                "runtime_changed_files": sorted(changed),
            },
        ),
        ("attempts.json", attempts),
    ]:
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        )
    (output / "aggregation.sql").write_text(SQL + ";\n")
    if incidents:
        incident_folder = output / "incidents"
        incident_folder.mkdir()
        evidence_index = []
        for source_root, run in incidents:
            for path in sorted(source_root.rglob("*")):
                if not path.is_file() or (
                    path.suffix != ".log"
                    and not path.name.endswith(".json.gz")
                ):
                    continue
                original = path.read_bytes()
                # Never preserve a credential-bearing command line in the shared report.
                payload = (
                    original
                    if path.name.endswith(".json.gz")
                    else original.replace(
                        b"perf_fixture", b"<synthetic-password-redacted>"
                    )
                )
                target = incident_folder / (run["folder"] + "-" + path.name)
                if path.name.endswith(".json.gz"):
                    target.write_bytes(payload)
                else:
                    target = target.with_suffix(target.suffix + ".gz")
                    target.write_bytes(gzip.compress(payload, mtime=0))
                evidence_index.append(
                    {
                        "file": str(target.relative_to(output)),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "original_sha256": hashlib.sha256(
                            original
                        ).hexdigest(),
                        "sanitized": payload != original,
                        "variant": run["variant"],
                        "profile": run["profile"],
                        "users": run["users"],
                        "repeat": run["repeat"],
                    }
                )
        (output / "incident-index.json").write_text(
            json.dumps(evidence_index, indent=2) + "\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--supplement", type=Path, nargs="*", default=[])
    args = parser.parse_args()
    export(args.capture, args.output, args.supplement)

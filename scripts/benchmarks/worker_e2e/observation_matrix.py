"""Sequential matched Worker comparison: zero-delay models, fixed resource limits."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

TOOLS = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--redis-url", required=True)
    for variant in ("baseline", "candidate"):
        parser.add_argument("--" + variant + "-root", type=Path, required=True)
        parser.add_argument("--" + variant + "-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--users",
        type=int,
        nargs="+",
        choices=(1, 10, 30, 50),
        default=[1, 10, 30, 50],
    )
    parser.add_argument(
        "--trial-indices",
        type=int,
        nargs="+",
        choices=(1, 2, 3),
        default=[1, 2, 3],
    )
    args = parser.parse_args()
    assert not args.output.exists(), "Use fresh evidence folder"
    args.output.mkdir(parents=True)
    assert len(set(args.users)) == len(args.users) and len(
        set(args.trial_indices)
    ) == len(args.trial_indices)
    conditions = (
        [
            (profile, users, 1)
            for users in args.users
            for profile in ("standard", "large20")
        ]
        if 1 in args.trial_indices
        else []
    )
    conditions += [
        (profile, 50, repeat)
        for repeat in args.trial_indices
        if repeat != 1 and 50 in args.users
        for profile in ("standard", "large20")
    ]
    assert conditions, "No selected conditions"
    paths = [
        TOOLS / name
        for name in (
            "run.py",
            "server.py",
            "model_fixture.py",
            "observation_scenarios.py",
            "checkpoint_profile.py",
        )
    ]
    paths.append(TOOLS.parent / "executor_throughput/mock_executor.py")
    digests = {
        str(path.relative_to(TOOLS.parent)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }
    runs = []
    for index, (profile, users, repeat) in enumerate(conditions):
        # Alternate direction and repeat order; never measure both services together.
        variants = (
            ("baseline", "candidate")
            if (index // 2 + repeat) % 2
            else ("candidate", "baseline")
        )
        for variant in variants:
            name = f"{variant}-{profile}-u{users}-r{repeat}"
            output = args.output / name
            cmd = [
                sys.executable,
                str(TOOLS / "run.py"),
                "--database-url",
                args.database_url,
                "--redis-url",
                args.redis_url,
                "--source-root",
                str(getattr(args, variant + "_root")),
                "--source-commit",
                getattr(args, variant + "_commit"),
                "--output",
                str(output),
                "--scenario",
                "executor",
                "--observation-profile",
                profile,
                "--users",
                str(users),
                "--trial-index",
                str(repeat),
                "--concurrency",
                "20",
                "--delay-ms",
                "0",
                "--checkpoint-profile",
            ]
            assert digests == {
                str(path.relative_to(TOOLS.parent)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in paths
            }, "Changed measurement harness"
            print("START " + name, flush=True)
            began = time.monotonic()
            with (args.output / (name + ".log")).open("w") as log:
                result = subprocess.run(
                    cmd, stdout=log, stderr=subprocess.STDOUT
                )
            # Do not store credential-bearing command lines in shareable evidence.
            runs.append(
                {
                    "variant": variant,
                    "profile": profile,
                    "users": users,
                    "repeat": repeat,
                    "folder": name,
                    "process_seconds": time.monotonic() - began,
                    "exit_code": result.returncode,
                }
            )
            (args.output / "runs.json").write_text(
                json.dumps({"runs": runs, "harness_sha256": digests}, indent=2)
                + "\n"
            )
            result.check_returncode()
            print(
                "DONE " + name,
                round(runs[-1]["process_seconds"], 2),
                flush=True,
            )


if __name__ == "__main__":
    main()

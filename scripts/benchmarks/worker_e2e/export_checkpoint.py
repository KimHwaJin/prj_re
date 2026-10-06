"""Portable selected-row evidence, no live DB required for verification."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean, stdev
from analyze_checkpoint import analyze_checkpoint


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def export(inputs, output):
    output.mkdir(exist_ok=False, parents=True)
    (output / "raw").mkdir()
    rows = []
    manifest = []
    fingerprints = []
    for label, directory in inputs:
        for path in sorted(directory.rglob("raw.json")):
            original = path.read_bytes()
            raw = json.loads(original)
            summary = analyze_checkpoint(raw)
            name = label + "-" + path.parent.name + ".json.gz"
            packed = gzip.compress(original, mtime=0)
            (output / "raw" / name).write_bytes(packed)
            rows.append(dict(study=label, raw=name, **summary))
            manifest.append(
                dict(
                    file="raw/" + name,
                    sha256=hashlib.sha256(packed).hexdigest(),
                    raw_sha256=hashlib.sha256(original).hexdigest(),
                    source_commit=raw["config"]["source_commit"],
                    users=raw["config"]["users"],
                    repeat=raw["config"]["repeat"],
                )
            )
            fingerprints.append(raw["config"]["source_sha256"])
    assert len(rows) > 0 and all(f == fingerprints[0] for f in fingerprints), (
        "Runtime source changed across measurement"
    )
    keys = [
        (r["study"], r["threads"], r["flow"]["followup"], r["flow"]["repeat"])
        for r in rows
    ]
    assert len(keys) == len(set(keys)), "Duplicate trial identity"
    main = [r for r in rows if r["study"] in ("e2e", "repeat")]
    fifty = [r for r in main if r["threads"] == 50]
    assert len(fifty) == 3 and {r["flow"]["repeat"] for r in fifty} == {
        1,
        2,
        3,
    }
    assert {r["threads"] for r in main} == {1, 10, 30, 50}
    dump(output / "results.json", rows)
    dump(output / "manifest.json", manifest)
    dump(output / "runtime-source-sha256.json", fingerprints[0])
    dump(
        output / "rollup.json",
        dict(
            main_trials=len(main),
            main_users=sum(r["threads"] for r in main),
            main_50=dict(
                repeats=3,
                mean_seconds=mean(r["flow"]["mean_seconds"] for r in fifty),
                trial_mean_sample_std_seconds=stdev(
                    r["flow"]["mean_seconds"] for r in fifty
                ),
                saver_write_ms_per_user=mean(
                    r["saver_write_ms_per_user"] for r in fifty
                ),
                saver_write_outer_ms_per_user=mean(
                    r["saver_write_outer_ms_per_user"] for r in fifty
                ),
                read_ms_per_user=mean(
                    r["timings"]["aget_tuple"]["ms_per_user"] for r in fifty
                ),
                serialization_ms_per_user=mean(
                    r["serialization_ms_per_user"] for r in fifty
                ),
                logical_payload_bytes_per_user=mean(
                    r["logical_payload_bytes_per_user"] for r in fifty
                ),
            ),
            all_trials=len(rows),
            all_users=sum(r["threads"] for r in rows),
            validated=True,
        ),
    )
    # Independent archive arithmetic and exact raw/persisted identity checks.
    for item in manifest:
        packed = (output / item["file"]).read_bytes()
        original = gzip.decompress(packed)
        raw = json.loads(original)
        assert hashlib.sha256(packed).hexdigest() == item["sha256"]
        assert hashlib.sha256(original).hexdigest() == item["raw_sha256"]
        r = next(r for r in rows if r["raw"] == Path(item["file"]).name)
        p = raw["checkpoint_profile"]
        total = sum(v["bytes"] for v in p["blobs"]) + sum(
            v["bytes"] for v in p["writes"]
        )
        total += sum(
            v["checkpoint_json_bytes"] + v["metadata_json_bytes"]
            for v in p["checkpoints"]
        )
        assert total == r["logical_payload_bytes"]
        assert (
            abs(
                sum(v["seconds"] for v in raw["results"]) / item["users"]
                - r["flow"]["mean_seconds"]
            )
            < 1e-9
        )
    dump(
        output / "verification.json",
        dict(
            archive_sha_and_raw_sha_verified=True,
            arithmetic_recomputed_from_rows=True,
            runtime_sources_identical=True,
            trials=len(rows),
            users=sum(r["threads"] for r in rows),
        ),
    )
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--capture",
        action="append",
        required=True,
        help="label=/absolute/capture/root",
    )
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    inputs = [
        (value.split("=", 1)[0], Path(value.split("=", 1)[1]))
        for value in args.capture
    ]
    rows = export(inputs, args.output)
    print(
        json.dumps(
            dict(
                validated_trials=len(rows),
                users=sum(r["threads"] for r in rows),
            )
        )
    )

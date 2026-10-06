"""Prove independent validation rejects corruptions of an actual capture."""

import argparse
from copy import deepcopy
import gzip
import json
import math
from pathlib import Path
import tempfile

from verify_observation_comparison import derive, verify


def controls(folder):
    index = json.loads((folder / "raw-index.json").read_text())
    entry = next(
        r
        for r in index
        if r["variant"] == "candidate"
        and r["profile"] == "large20"
        and r["users"] == 1
    )
    raw = json.loads(gzip.decompress((folder / entry["file"]).read_bytes()))
    expected = derive(raw)
    rejected = []

    def reject(name, mutation, metric=None):
        changed = deepcopy(raw)
        mutation(changed)
        try:
            actual = derive(changed)
            if metric:
                assert math.isclose(
                    actual[metric],
                    expected[metric],
                    rel_tol=1e-10,
                    abs_tol=1e-8,
                )
        except AssertionError:
            rejected.append(name)
        else:
            raise AssertionError("Corruption accepted: " + name)

    reject("missing user", lambda r: r["results"].pop())
    reject(
        "duplicated final evidence",
        lambda r: r["results"][0]["observations"].append(
            deepcopy(r["results"][0]["observations"][0])
        ),
    )
    reject("wrong model delay", lambda r: r["config"].update(delay_ms=5000))
    reject("wrong capacity", lambda r: r["config"].update(total_capacity=32))
    reject(
        "duplicated successful event",
        lambda r: r["server"]["event_handlers"].append(
            deepcopy(
                next(
                    e for e in r["server"]["event_handlers"] if not e["error"]
                )
            )
        ),
    )
    reject(
        "wrong persisted byte count",
        lambda r: r["checkpoint_profile"]["blobs"][0].update(
            bytes=r["checkpoint_profile"]["blobs"][0]["bytes"] + 1048576
        ),
        "logical_mib_per_user",
    )
    with tempfile.TemporaryDirectory(
        prefix="observation-validator-"
    ) as temporary:
        target = Path(temporary)
        for name in (
            "results.json",
            "summary.json",
            "raw-index.json",
            "source-audit.json",
            "attempts.json",
            "incident-index.json",
        ):
            (target / name).symlink_to((folder / name).resolve())
        (target / "incidents").symlink_to(
            (folder / "incidents").resolve(), target_is_directory=True
        )
        (target / "raw").mkdir()
        for record in index:
            path = target / record["file"]
            if record is entry:
                payload = gzip.decompress(
                    (folder / record["file"]).read_bytes()
                )
                path.write_bytes(
                    gzip.compress(
                        payload.replace(
                            b'"passed": true', b'"passed":false', 1
                        ),
                        mtime=0,
                    )
                )
                assert gzip.decompress(path.read_bytes()) != payload
            else:
                path.symlink_to((folder / record["file"]).resolve())
        try:
            verify(target)
        except AssertionError:
            rejected.append("archive payload SHA mismatch")
        else:
            raise AssertionError("Archive corruption accepted")
    assert len(rejected) == 7
    return {"rejected_controls": rejected, "passed": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    folder = parser.parse_args().folder
    result = controls(folder)
    (folder / "negative-controls.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))

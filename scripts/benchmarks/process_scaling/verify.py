"""Independent arithmetic and cohort checks for the published snapshot."""

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics

p = argparse.ArgumentParser()
p.add_argument("folder", type=Path)
a = p.parse_args()
rows = json.loads((a.folder / "results.json").read_text())
manifest = {
    r["raw"]: r for r in json.loads((a.folder / "manifest.json").read_text())
}
checks = []
for r in rows:
    data = gzip.decompress((a.folder / r["raw"]).read_bytes())
    assert (
        hashlib.sha256(data).hexdigest()
        == manifest[r["raw"]]["normalized_sha256"]
    )
    raw = json.loads(data)
    n = r["users"]
    times = sorted(s["ms"] / 1000 for s in raw["scenarios"])
    assert len(times) == n and all(
        s["status"] == "complete" for s in raw["scenarios"]
    )
    assert len({s["user"] for s in raw["scenarios"]}) == n
    assert math.isclose(
        statistics.mean(times), r["per_user"]["total_s"]["mean"]
    )
    assert times[math.ceil(n * 0.95) - 1] == r["per_user"]["total_s"]["p95"]
    assert math.isclose(
        n / max(s["finished_s"] for s in raw["scenarios"]),
        r["throughput_users_s"],
    )
    assert math.isclose(
        sum(s["queue_ms"] for s in raw["database"]["runs"]) / 1000 / n,
        r["per_user"]["queue_s"]["mean"],
    )
    assert math.isclose(
        sum(w["cpu_seconds"] for w in raw["per_process"]) / n,
        r["worker_cpu_seconds_per_user"],
    )
    assert math.isclose(
        max(
            s["worker_rss_bytes"] + s["supervisor_rss_bytes"]
            for s in raw["resource_samples"]
        )
        / 1024**2,
        r["api_rss_peak_mib"],
    )
    assert (
        max(s["db_connections"] for s in raw["resource_samples"])
        == r["db_connections"]["max"]
    )
    assert (
        len(raw["stages"])
        == len(raw["server"]["workers"])
        == len(raw["llm"]["events"])
        == 4 * n
    )
    assert raw["config"]["commit"] == "c3534f0"
    checks.append(
        dict(layout=r["layout"], users=n, repeat=r["repeat"], status="passed")
    )
assert {(r["layout"], r["users"]) for r in rows} == {
    (l, n)
    for l in ("1×4", "1×8", "2×4", "1×16", "2×8", "4×4")
    for n in (10, 30, 50)
}
if (a.folder / "table.json").exists():
    for row in json.loads((a.folder / "table.json").read_text()):
        chosen = [
            r
            for r in rows
            if r["layout"] == row["layout"] and r["users"] == row["users"]
        ]
        assert row["repeats"] == len(chosen)
        assert math.isclose(
            row["mean_s"],
            statistics.mean(r["per_user"]["total_s"]["mean"] for r in chosen),
        )
        assert math.isclose(
            row["p95_s"],
            statistics.mean(r["per_user"]["total_s"]["p95"] for r in chosen),
        )
        assert math.isclose(
            row["rss_mib"],
            statistics.mean(r["api_rss_peak_mib"] for r in chosen),
        )
(a.folder / "independent-checks.json").write_text(
    json.dumps(checks, indent=2) + "\n"
)
print(
    f"Validated {len(rows)} trials / {sum(r['users'] for r in rows)} users and report aggregates"
)

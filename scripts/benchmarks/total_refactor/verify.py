"""Independent relational/raw audit of report aggregates and archive integrity."""

import argparse, gzip, hashlib, json, math, sqlite3, statistics
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--data", type=Path, required=True)
a = p.parse_args()
summary = {
    r["trial"]: r for r in json.loads((a.data / "summary.json").read_text())
}
manifest = json.loads((a.data / "manifest.json").read_text())
checks = []


def equal(x, y):
    return (
        x is None
        and y is None
        or x is not None
        and y is not None
        and math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-7)
    )


for m in manifest:
    packed = (a.data / m["path"]).read_bytes()
    raw = gzip.decompress(packed)
    r = json.loads(raw)
    d = summary[m["trial"]]
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE scenario(user INTEGER,status TEXT,ms REAL); "
        "CREATE TABLE request(kind TEXT,status INTEGER,ms REAL); "
        "CREATE TABLE stage(user INTEGER,run_id TEXT,name "
        "TEXT,status TEXT); CREATE TABLE run(run_id TEXT PRIMARY "
        "KEY,queue_ms REAL,execution_ms "
        "REAL);"
    )
    db.executemany(
        "INSERT INTO scenario VALUES (?,?,?)",
        [(x["user"], x["status"], x["ms"]) for x in r["scenarios"]],
    )
    db.executemany(
        "INSERT INTO request VALUES (?,?,?)",
        [(x["kind"], x["status"], x["ms"]) for x in r["requests"]],
    )
    db.executemany(
        "INSERT INTO stage VALUES (?,?,?,?)",
        [
            (x["user"], x["run_id"], x["stage"], x["status"])
            for x in r["stages"]
        ],
    )
    db.executemany(
        "INSERT INTO run VALUES (?,?,?)",
        [
            (x["run_id"], x["queue_ms"], x["execution_ms"])
            for x in r["database"]["runs"]
        ],
    )
    count, avg = db.execute(
        "SELECT count(*),avg(ms)/1000 FROM scenario WHERE status='complete'"
    ).fetchone()
    queue = db.execute(
        "SELECT avg(q) FROM (SELECT s.user,sum(coalesce(r.queue_ms,0"
        "))/1000 AS q FROM scenario s JOIN stage t USING(user) JOIN "
        "run r USING(run_id) WHERE s.status='complete' GROUP BY "
        "s.user)"
    ).fetchone()[0]
    c = {
        "trial": m["trial"],
        "compressed_sha256": hashlib.sha256(packed).hexdigest()
        == m["gzip_sha256"],
        "raw_sha256": hashlib.sha256(raw).hexdigest() == m["raw_sha256"],
        "completed_count": count == d["completed"],
        "scenario_mean_sql": equal(avg, d["completion_seconds"]["mean"]),
        "queue_mean_sql": equal(
            queue, d["completed_user_queue_seconds"]["mean"]
        ),
        "requests_count_sql": db.execute(
            "SELECT count(*) FROM request"
        ).fetchone()[0]
        == sum(x["requests"] for x in d["requests"].values()),
        "slot_bound": d["peak_graph"] <= r["config"]["slots"],
        "resource_counts": all(
            d["resource_counts"].get(op, 0)
            == sum(x["operation"] == op for x in r["server"]["resources"])
            for op in ("graph_build", "pool_construct", "pool_close")
        ),
        "model_modes": sum(d["model_modes"].values())
        == len(r["server"]["models"]),
        "fixed_http_delay": all(4990 <= x["ms"] for x in r["llm"]["events"]),
    }
    # Independent percentile calculation by rank interpolation.
    for kind in d["requests"]:
        vals = [
            v[0]
            for v in db.execute(
                (
                    "SELECT ms FROM request WHERE kind=? AND "
                    "status>=200 AND status<300 ORDER BY "
                    "ms"
                ),
                (kind,),
            )
        ]
        if vals:
            rank = 0.95 * (len(vals) - 1)
            lo = int(rank)
            p95 = vals[lo] + (vals[min(lo + 1, len(vals) - 1)] - vals[lo]) * (
                rank - lo
            )
        else:
            p95 = None
        c["p95_sql_" + kind] = equal(
            p95, d["requests"][kind]["success_ms"]["p95"]
        )
    checks.append(c)
    db.close()
result = {
    "passed": all(
        all(v for k, v in c.items() if k != "trial") for c in checks
    ),
    "method": (
        "Independent gzip SHA-256 and SQLite joins/aggregations over "
        "raw records, plus independent rank interpolation. No copied "
        "summary cells."
    ),
    "trials": len(checks),
    "checks": checks,
}
(a.data / "independent-validation.json").write_text(
    json.dumps(result, indent=2)
)
print(json.dumps({"passed": result["passed"], "trials": len(checks)}))
if not result["passed"]:
    raise SystemExit(1)

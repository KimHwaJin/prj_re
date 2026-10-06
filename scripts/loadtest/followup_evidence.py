"""Normalize raw suite evidence and independently aggregate report datasets in SQLite."""

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from build_crud_report import memory_mib

HTTP_METHODS = ("GET", "POST", "PATCH", "DELETE")


def read(path):
    return json.loads(Path(path).read_text())


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build(
    root=Path("var/loadtest"),
    output=Path("docs/reports/service-followup-2026-09-28"),
):
    mixed = root / "crud-mixed-soak-20260928"
    agent = root / "approval-ramp-20260928"
    mm = read(mixed / "metadata.json")
    am = read(agent / "metadata.json")
    assert "finished_at" in mm and "finished_at" in am, (
        "Suite has not finished"
    )
    ms = read(mixed / "stages.json")
    aps = read(agent / "stages.json")
    assert [s["users"] for s in aps] == [1, 5, 10, 25, 50, 75, 100]
    output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output / "evidence.sqlite")
    db.row_factory = sqlite3.Row
    db.executescript("""
      DROP TABLE IF EXISTS http_stats; DROP TABLE IF EXISTS flow_stats;
      DROP TABLE IF EXISTS minutes; DROP TABLE IF EXISTS resource_samples; DROP TABLE IF EXISTS queue_samples;
      DROP TABLE IF EXISTS runs;
      CREATE TABLE http_stats(scenario TEXT,users INTEGER,seconds REAL,method TEXT,path TEXT,requests INTEGER,failures INTEGER,avg_ms REAL,p50_ms REAL,p95_ms REAL,p99_ms REAL,max_ms REAL);
      CREATE TABLE flow_stats(scenario TEXT,users INTEGER,seconds REAL,name TEXT,requests INTEGER,failures INTEGER,avg_ms REAL,p95_ms REAL,p99_ms REAL,max_ms REAL);
      CREATE TABLE minutes(minute INTEGER,elapsed REAL,requests INTEGER,failures INTEGER,response_sum REAL);
      CREATE TABLE resource_samples(scenario TEXT,users INTEGER,minute INTEGER,service TEXT,cpu REAL,memory REAL);
      CREATE TABLE queue_samples(scenario TEXT,users INTEGER,connections INTEGER,agent_connections INTEGER,pending INTEGER,running INTEGER,locks INTEGER);
      CREATE TABLE runs(users INTEGER,node TEXT,run_id TEXT,queue_ms REAL,execution_ms REAL,attempts INTEGER);
    """)
    for scenario, stages in [("crud_mixed", ms), ("approval", aps)]:
        for stage in stages:
            assert all(
                s["data"]["user_count"] == stage["users"]
                for s in stage["samples"]
            )
            for row in stage["stats"]["stats"]:
                if not row["num_requests"]:
                    continue
                if row["method"] in HTTP_METHODS:
                    db.execute(
                        (
                            "INSERT INTO http_stats "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                        ),
                        (
                            scenario,
                            stage["users"],
                            stage["elapsed_seconds"],
                            row["method"],
                            row["name"],
                            row["num_requests"],
                            row["num_failures"],
                            row["avg_response_time"],
                            row["median_response_time"],
                            row["response_time_percentile_0.95"],
                            row["response_time_percentile_0.99"],
                            row["max_response_time"],
                        ),
                    )
                elif row["method"] == "FLOW":
                    db.execute(
                        "INSERT INTO flow_stats VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            scenario,
                            stage["users"],
                            stage["elapsed_seconds"],
                            row["name"],
                            row["num_requests"],
                            row["num_failures"],
                            row["avg_response_time"],
                            row["response_time_percentile_0.95"],
                            row["response_time_percentile_0.99"],
                            row["max_response_time"],
                        ),
                    )
    for minute in range(1, 16):
        sample = next(
            s for s in ms[0]["samples"] if s["elapsed"] >= minute * 60 - 0.1
        )
        rows = [
            r for r in sample["data"]["stats"] if r["method"] in HTTP_METHODS
        ]
        db.execute(
            "INSERT INTO minutes VALUES(?,?,?,?,?)",
            (
                minute,
                sample["elapsed"],
                sum(r["num_requests"] for r in rows),
                sum(r["num_failures"] for r in rows),
                sum(r["num_requests"] * r["avg_response_time"] for r in rows),
            ),
        )
    for scenario, folder, stages in [
        ("crud_mixed", mixed, ms),
        ("approval", agent, aps),
    ]:
        samples = [
            json.loads(line)
            for line in (folder / "resources.jsonl").read_text().splitlines()
        ]
        for r in samples:
            if r["phase"] != "measure" or "error" in r:
                continue
            stage = next(
                (
                    s
                    for s in stages
                    if s["users"] == r["users"]
                    and s["started_at"] <= r["at"] <= s["finished_at"]
                ),
                None,
            )
            if not stage:
                continue
            minute = 1 + int(
                (stamp(r["at"]) - stamp(stage["started_at"])).total_seconds()
                // 60
            )
            for c in r["containers"]:
                for service, label in [
                    ("api", "API"),
                    ("postgres", "PostgreSQL"),
                    ("locust", "Locust"),
                ]:
                    if c["Name"] == f"dtest-agent-loadtest-{service}-1":
                        db.execute(
                            "INSERT INTO resource_samples VALUES(?,?,?,?,?,?)",
                            (
                                scenario,
                                r["users"],
                                minute,
                                label,
                                float(c["CPUPerc"].rstrip("%")),
                                memory_mib(c["MemUsage"]),
                            ),
                        )
            db.execute(
                "INSERT INTO queue_samples VALUES(?,?,?,?,?,?,?)",
                (
                    scenario,
                    r["users"],
                    r["database"]["connections"],
                    r.get("all_databases", {})
                    .get("agent", {})
                    .get("connections"),
                    r.get("runs", {}).get("pending"),
                    r.get("runs", {}).get("running"),
                    r["database"]["lock_waits"],
                ),
            )
    journeys = [
        json.loads(line)
        for line in (agent / "journeys.jsonl").read_text().splitlines()
    ]
    seen = set()
    for j in journeys:
        if not j["ok"]:
            continue
        assert not j.get("execution_id") and len(j["runs"]) == 4
        assert len({r["task_id"] for r in j["runs"]}) == 1
        for node, r in zip(
            [
                "data_selection",
                "analysis_context",
                "workflow_candidate_selection",
                "workflow_approval",
            ],
            j["runs"],
        ):
            assert r["id"] not in seen
            seen.add(r["id"])
            stage = next(
                (
                    s
                    for s in aps
                    if stamp(s["started_at"])
                    <= stamp(r["updated_at"])
                    <= stamp(s["finished_at"])
                ),
                None,
            )
            if not stage:
                continue
            q = (
                stamp(r["started_at"]) - stamp(r["created_at"])
            ).total_seconds() * 1000
            e = (
                stamp(r["updated_at"]) - stamp(r["started_at"])
            ).total_seconds() * 1000
            assert q >= 0 and e >= 0
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?)",
                (stage["users"], node, r["id"], q, e, r["attempt_count"]),
            )
    queries = {
        "http_summary": """SELECT scenario,users,MAX(seconds) seconds,SUM(requests) requests,SUM(failures) failures,
         ROUND(SUM(requests)/MAX(seconds),3) rps,ROUND(SUM(avg_ms*requests)/SUM(requests),3) avg_ms,
         MAX(max_ms) max_ms FROM http_stats GROUP BY scenario,users ORDER BY scenario,users""",
        "mixed_endpoints": """SELECT method||' '||path endpoint,method,path,requests,failures,
         ROUND(avg_ms,3) avg_ms,p50_ms,p95_ms,p99_ms,max_ms FROM http_stats WHERE scenario='crud_mixed'
         ORDER BY p95_ms DESC,method,path""",
        "agent_http_100": """SELECT method||' '||path endpoint,requests,failures,
         ROUND(avg_ms,3) avg_ms,p95_ms,p99_ms,max_ms FROM http_stats WHERE scenario='approval' AND users=100
         ORDER BY method,path""",
        "mixed_mix": """WITH c AS (SELECT SUBSTR(name,6,INSTR(SUBSTR(name,6),'/')-1) action,SUM(requests) requests FROM flow_stats WHERE scenario='crud_mixed' AND name LIKE 'CRUD/%' GROUP BY action)
         SELECT action,requests,ROUND(100.0*requests/SUM(requests) OVER(),2) actual_pct FROM c ORDER BY action""",
        "mixed_minutes": """WITH x AS (SELECT minute,elapsed-LAG(elapsed,1,0) OVER(ORDER BY minute) seconds,
          requests-LAG(requests,1,0) OVER(ORDER BY minute) requests,
          failures-LAG(failures,1,0) OVER(ORDER BY minute) failures,
          response_sum-LAG(response_sum,1,0) OVER(ORDER BY minute) response_sum FROM minutes)
         SELECT minute,seconds,requests,failures,ROUND(requests/seconds,3) rps,ROUND(response_sum/requests,3) avg_ms FROM x ORDER BY minute""",
        "mixed_memory": """SELECT minute,service,ROUND(AVG(memory),2) memory_mib,ROUND(MAX(memory),2) max_memory_mib,
          ROUND(AVG(cpu),2) cpu_pct,COUNT(*) samples FROM resource_samples WHERE scenario='crud_mixed' GROUP BY minute,service ORDER BY minute,service""",
        "agent_flow": """SELECT users,CAST(users AS TEXT)||'명' user_label,requests journeys,failures,
          ROUND((requests-failures)/seconds,3) successful_per_sec,ROUND(avg_ms/1000,3) avg_sec,
          ROUND(p95_ms/1000,3) p95_sec,ROUND(p99_ms/1000,3) p99_sec,ROUND(max_ms/1000,3) max_sec
          FROM flow_stats WHERE scenario='approval' AND name='SCENARIO/approval_wait' ORDER BY users""",
        "agent_nodes": """SELECT users,name,SUM(requests) runs,SUM(failures) failures,ROUND(AVG(avg_ms)/1000,3) avg_sec,
          ROUND(MAX(p95_ms)/1000,3) p95_sec,ROUND(MAX(p99_ms)/1000,3) p99_sec FROM flow_stats
          WHERE scenario='approval' AND name LIKE 'RUN/%' GROUP BY users,name ORDER BY users,name""",
        "run_queue": """WITH r AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY users ORDER BY queue_ms) rn,COUNT(*) OVER(PARTITION BY users) n FROM runs)
         SELECT users,COUNT(*) runs,ROUND(AVG(queue_ms)/1000,3) queue_avg_sec,
           ROUND(MIN(CASE WHEN rn>=n*0.95 THEN queue_ms END)/1000,3) queue_p95_sec,
           ROUND(AVG(execution_ms)/1000,3) execution_avg_sec,MAX(attempts) max_attempts,
           ROUND(SUM(queue_ms)/SUM(queue_ms+execution_ms)*100,2) queue_share_pct
         FROM r GROUP BY users ORDER BY users""",
        "resources": """SELECT scenario,users,service,ROUND(AVG(cpu),2) cpu_avg,MAX(cpu) cpu_max,
          ROUND(MIN(memory),2) memory_min,ROUND(MAX(memory),2) memory_max,COUNT(*) samples
          FROM resource_samples GROUP BY scenario,users,service ORDER BY scenario,users,service""",
        "queues": """SELECT scenario,users,MAX(connections) connections_max,MAX(agent_connections) agent_connections_max,
          MAX(pending) pending_max,MAX(running) running_max,SUM(CASE WHEN locks>0 THEN 1 ELSE 0 END) lock_samples
          FROM queue_samples GROUP BY scenario,users ORDER BY scenario,users""",
    }
    datasets = {}
    sources = []
    for key, sql in queries.items():
        datasets[key] = [dict(r) for r in db.execute(sql)]
        source_path = output / (key + ".sql")
        source_path.write_text(sql + ";\n")
        sources.append(
            {
                "id": key + "_source",
                "label": key + " · suite evidence SQLite",
                "path": str(source_path),
                "query": {
                    "engine": "SQLite",
                    "language": "sql",
                    "sql": sql,
                    "tables_used": [
                        "http_stats",
                        "flow_stats",
                        "minutes",
                        "resource_samples",
                        "queue_samples",
                        "runs",
                    ],
                    "description": (
                        "crud_mixed 100명 900초 및 approval 7단계 각 "
                        "60초의 원시 Locust·Docker·DB·journey 관측 "
                        "집계."
                    ),
                    "filters": [
                        "단계별 안정화 이후 측정 구간",
                        "HTTP와 FLOW 별도 집계",
                        (
                            "큐 지연은 기록된 성공 여정의 각 Run 완료 "
                            "시각으로 측정 구간에 "
                            "배정"
                        ),
                    ],
                },
            }
        )
    db.commit()
    db.close()
    facts = {
        "mixed_metadata": mm,
        "agent_metadata": am,
        "successful_journeys_all": sum(j["ok"] for j in journeys),
        "failed_journeys_all": sum(not j["ok"] for j in journeys),
        "journey_errors": [j for j in journeys if not j["ok"]],
        "monitor_errors": {
            folder.name: sum(
                "error" in json.loads(line)
                for line in (folder / "resources.jsonl")
                .read_text()
                .splitlines()
            )
            for folder in (mixed, agent)
        },
        "stalled_run": read(agent / "stalled-run-diagnosis.json"),
        "final_verification": read(agent / "final-verification.json"),
        "baseline": read(root / "crud-ramp-20260928/summary.json"),
    }
    (output / "datasets.json").write_text(
        json.dumps(datasets, ensure_ascii=False, indent=2)
    )
    (output / "facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2)
    )
    return datasets, sources, facts

"""Preserve a stopped local trial, then cancel only its aged synthetic Runs."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from uuid import UUID

import httpx


def sql(query):
    return json.loads(
        subprocess.check_output(
            [
                "docker",
                "exec",
                "dtest-agent-loadtest-postgres-1",
                "psql",
                "-U",
                "dtest",
                "-d",
                "chat_app",
                "-Atc",
                query,
            ],
            text=True,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path)
    args = parser.parse_args()
    trial = args.trial
    metadata = json.loads((trial / "metadata.json").read_text())
    start = datetime.fromisoformat(metadata["started_at"]).isoformat()
    state = httpx.get("http://127.0.0.1:18089/stats/requests").json()
    assert state["state"] == "stopped" and state["user_count"] == 0, (
        "Stop load generator first"
    )
    rows = sql(f"""SELECT COALESCE(json_agg(r),'[]'::json) FROM (
        SELECT run_id,session_id,status,created_at,started_at,completed_at,updated_at,attempt_count
        FROM agent_runs WHERE created_at >= '{start}'::timestamptz ORDER BY created_at
    ) r""")
    (trial / "database-runs-before-cleanup.json").write_text(
        json.dumps(rows, indent=2)
    )
    traces = trial.parent / "traces"
    traces.mkdir(exist_ok=True)

    def copy_traces():
        subprocess.run(
            [
                "docker",
                "cp",
                "dtest-agent-loadtest-api-1:/app/var/diagnostics/phase1-20260928/.",
                str(traces),
            ],
            check=True,
        )

    copy_traces()
    active = sql("""SELECT COALESCE(json_agg(r),'[]'::json) FROM (
        SELECT a.run_id,a.session_id,a.status,a.created_at,a.started_at,s.user_id,s.session_name,u.user_name,
          extract(epoch FROM now()-a.started_at) age_seconds
        FROM agent_runs a JOIN sessions s USING(session_id) JOIN users u ON u.user_id=s.user_id
        WHERE a.status IN ('pending','running')
    ) r""")
    evidence = {
        "at": datetime.now(timezone.utc).isoformat(),
        "active_before": active,
        "cancellations": [],
    }
    # Validate the entire set before performing any cancellation.
    for r in active:
        assert datetime.fromisoformat(
            r["created_at"]
        ) >= datetime.fromisoformat(start)
        assert r["session_name"] == "LLM mock load scenario" and r[
            "user_name"
        ].startswith("load-")
        assert r["status"] == "running" and r["age_seconds"] > 120, (
            "Unexpected active Run; inspect manually"
        )
        for key in ("run_id", "session_id", "user_id"):
            UUID(r[key])
    (trial / "cleanup.json").write_text(json.dumps(evidence, indent=2))
    for r in active:
        response = httpx.post(
            f"http://127.0.0.1:18080/api/v1/sessions/{r['session_id']}/runs/{r['run_id']}/cancel",
            headers={"Authorization": "Bearer " + r["user_id"]},
            json={
                "reason": (
                    "Polling comparison: preserve stall evidence, then "
                    "drain synthetic "
                    "trial"
                )
            },
            timeout=30,
        )
        response.raise_for_status()
        evidence["cancellations"].append(
            {
                "run_id": r["run_id"],
                "http_status": response.status_code,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
    deadline = time.monotonic() + 30
    while True:
        count = sql(
            "SELECT count(*) FROM agent_runs WHERE status IN "
            "('pending','running')"
        )
        if count == 0:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Synthetic Runs did not drain after cancellation"
            )
        time.sleep(0.5)
    evidence["active_after"] = count
    evidence["executor"] = httpx.get("http://127.0.0.1:18081/health").json()
    assert (
        evidence["executor"]["unique_submissions"]
        == metadata["before_mock_executor"]["unique_submissions"]
    )
    (trial / "cleanup.json").write_text(json.dumps(evidence, indent=2))
    copy_traces()
    print(
        json.dumps(
            {
                "trial": trial.name,
                "runs": len(rows),
                "canceled_stalls": len(active),
                "active_after": count,
                "executor": evidence["executor"],
            }
        )
    )


if __name__ == "__main__":
    main()

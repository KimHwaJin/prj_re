"""Isolated HTTP/SQL/checkpoint/Redis + actual Executor/Jupyter plan revision verification.

Uses test-only registered functions and a preset initial plan to exercise repeatable
revision requests. --real selects a real revision role, not initial planning; all other modes
use an explicit deterministic revision-role double. Production assets are unchanged.
"""

from cookie_auth import (
    configure_cookie_auth,
    install_employee_fixture,
    sign_in,
    write_private_result,
)
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy.engine import make_url
import uvicorn
import yaml

from dtest.settings.loader import load_settings
from dtest.bootstrap import create_app
from dtest.application.runs.runtime import runtime as graph_runtime
from dtest.settings.agent import build_langgraph_thread_id
from tests.agent_service.test_agentic_repair import FixtureCatalog, document
from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import (
    reply_schema,
)
from tests.agent_service.test_plan_revision import revision_reply
from dtest.agent_service.agents.analysis.planning.proposals import (
    RevisionReply,
)
from dataclasses import replace

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--settings-file", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument(
    "--real",
    action="store_true",
    help="Real model for revision role only; initial plan is a preset fixture.",
)
parser.add_argument("--port", type=int, default=18093)
parser.add_argument(
    "--scenarios",
    nargs="+",
    choices=["registered", "free", "clarification", "modified"],
)
parser.add_argument(
    "--no-approval",
    action="store_true",
    help="Disable only complete single free-code plan approval in this isolated server.",
)
parser.add_argument(
    "--structured-output-mode",
    choices=["prompt_json", "provider_json_schema"],
    default="prompt_json",
)
args = parser.parse_args()
scenarios = args.scenarios or (
    ["free"]
    if args.real
    else ["registered", "free", "clarification", "modified"]
)
config = json.loads(args.settings_file.read_text())
base = config.get("EXECUTOR_BASE_URL", "http://127.0.0.1:8000")
assert urlparse(base).hostname in {"127.0.0.1", "localhost"}
for key, name in [
    ("database_url", "agentic_runtime_test"),
    ("checkpoint_db_uri", "agentic_checkpoint_test"),
]:
    value = config.get(key, config.get(key.upper()))
    assert make_url(value).database == name and make_url(value).host in {
        "127.0.0.1",
        "localhost",
    }
assert config.get("EXECUTOR_SHARED_RESULT_ROOT")
namespace = "agentic-revision-" + uuid4().hex[:10]
original_dns = socket.getaddrinfo
aliases = {
    "model.frodo.com": "10.250.110.99",
    "phoenix.frodo.com": "10.250.110.100",
}
socket.getaddrinfo = lambda host, *a, **kw: original_dns(
    aliases.get(host, host), *a, **kw
)
config.update(
    MODEL_PROVIDER="openai_compatible" if args.real else "mock",
    PHOENIX_PROJECT_NAME=namespace,
    EXECUTOR_SUBMIT_ENABLED=True,
    EXECUTOR_BASE_URL=base,
    EXECUTOR_SOURCE_TYPE="INLINE",
    EXECUTOR_RUNTIME_PROFILE="default",
    EXECUTOR_OPERATION_TIMEOUT_SECONDS=120,
    EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS=180,
    REDIS_URL="redis://127.0.0.1:6379/0",
    EW_NAMESPACE=namespace,
    EW_HEALTH_PORT=0,
    EW_CONCURRENCY=2,
    EW_POOL_SIZE=4,
    EW_POLL_SECONDS=0.1,
    EW_IDLE_POLL_SECONDS=0.2,
    EVENT_WORKER_ENABLED=True,
    AGENT_WORKER_ENABLED=True,
    AGENT_WORKER_CONCURRENCY=2,
    AGENT_WORKER_POLL_INTERVAL_SECONDS=0.1,
    TASK_RECONCILER_ENABLED=False,
    CHECKPOINT_SETUP_ON_START=True,
    AGENT_REPAIR_LEVEL=0,
    AGENT_REPAIR_LEVEL_LIMIT=4,
    AGENT_MAX_REPAIR_ATTEMPTS=2,
    AGENT_FREE_PLAN_REQUIRE_APPROVAL=not args.no_approval,
)
config["MODEL_STRUCTURED_OUTPUT_MODE"] = args.structured_output_mode
if config.get("MODEL_CATALOG"):
    entries = (
        json.loads(config["MODEL_CATALOG"])
        if isinstance(config["MODEL_CATALOG"], str)
        else deepcopy(config["MODEL_CATALOG"])
    )
    for entry in entries.values():
        entry["structured_output_mode"] = args.structured_output_mode
    config["MODEL_CATALOG"] = entries
root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="agentic-repair-migrations-") as temp:
    path = Path(temp) / "config.yml"
    path.write_text(yaml.safe_dump(config))
    path.chmod(0o600)
    for ini in ("alembic.crud.ini", "alembic.ini"):
        outcome = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"],
            cwd=root,
            env={
                **os.environ,
                "SERVICE_CONFIG_FILE": str(path),
                "PYTHONPATH": str(root / "src"),
            },
            capture_output=True,
            text=True,
        )
        if outcome.returncode:
            raise RuntimeError(
                "Isolated migration failed: " + outcome.stderr[-1500:]
            )
configure_cookie_auth(config, namespace, args.port)
settings = load_settings(config=config, environ={})

original_inputs = graph_runtime._load_graph_inputs


def load_inputs():
    runtime, agent, kind = original_inputs()
    runtime.catalog = FixtureCatalog()

    async def preset(state, *rest):
        doc = document(0, 0)
        doc["steps"][1]["arguments"]["divisor"]["value"] = 2
        return reply_schema(runtime.catalog, 1)(
            kind="plans",
            message="진단용 최초 등록 계획입니다.",
            plans=[{"definition": doc}],
        )

    runtime.respond = preset
    if not args.real:

        async def revise(state, *rest):
            scenario = state["user_request"]
            if (
                scenario == "clarification"
                and state["planning_revision_count"] == 1
            ):
                return RevisionReply(
                    kind="clarification",
                    message="어떤 변환 방법을 원하시나요?",
                    plans=[],
                )
            reply = revision_reply(free=scenario != "registered")
            if scenario == "free":
                base = (
                    state.get("reviews") or state["planning_previous_reviews"]
                )
                full = reply.plans[0]
                reply = RevisionReply(
                    kind="plans",
                    message=reply.message,
                    plans=[
                        {
                            "base_plan_id": base[0]["plan_id"],
                            "patches": [
                                {
                                    "step_id": "transform",
                                    "tool_id": "custom.revised_transform",
                                }
                            ],
                            "functions": [
                                f.model_dump() for f in full.functions
                            ],
                        }
                    ],
                )
            if scenario == "modified":
                source = runtime.catalog.sources["repair_transform"][
                    "code"
                ].replace("x / divisor", "x / (divisor + 1)")
                reply.plans[0].functions[0].code = source
                reply.plans[0].functions[0].origin_tool_id = "repair_transform"
            return reply

        runtime.revise = revise
    return runtime, agent, kind


graph_runtime._load_graph_inputs = load_inputs
app = create_app(settings)
install_employee_fixture(app, namespace)
summary = {
    "corporate_sdk": "verified_employee_fixture",
    "production_cookie_csrf": True,
    "login_redis": "actual_loopback",
    "passed": False,
    "namespace": namespace,
    "real_revision_llm": args.real,
    "initial_planning": "preset_fixture",
    "structured_output_mode": args.structured_output_mode,
    "approval_required": not args.no_approval,
    "executor": "actual_local_compose",
    "scenarios": [],
}


async def main():
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.05)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{args.port}",
            trust_env=False,
            timeout=240,
        ) as client:
            user, headers = await sign_in(client)
            for scenario in scenarios:
                record = {"scenario": scenario, "passed": False}
                summary["scenarios"].append(record)
                r = await client.post(
                    "/api/v1/projects/"
                    + user["default_project_id"]
                    + "/sessions",
                    headers=headers,
                    json={
                        "session_name": scenario,
                        "settings": {"kernel_profile": "default"},
                    },
                )
                assert r.status_code == 201, r.text
                sid = r.json()["id"]
                path = "/api/v1/sessions/" + sid + "/runs"
                r = await client.post(
                    path,
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={
                        "input": {
                            "content": [{"type": "text", "text": scenario}]
                        }
                    },
                )
                assert r.status_code == 202, r.text
                rid = r.json()["run_id"]
                record.update(session_id=sid, run_id=rid)

                async def read():
                    response = await client.get(
                        path + "/" + rid, headers=headers
                    )
                    assert response.status_code == 200, response.text
                    return response.json()

                async def wait():
                    async with asyncio.timeout(240):
                        while True:
                            run = await read()
                            if run["status"] not in {"pending", "running"}:
                                return run
                            await asyncio.sleep(0.15)

                async def submit_action(run, action, key=None):
                    response = await client.post(
                        path,
                        headers={
                            **headers,
                            "Idempotency-Key": key or str(uuid4()),
                        },
                        json={
                            "run_id": rid,
                            "resume_token": run["resume_token"],
                            "command": {"resume": action},
                        },
                    )
                    assert response.status_code == 202, response.text

                run = await wait()
                assert run["status"] == "waiting_input", run
                form = run["interrupt"][0]
                old_plan = form["payload"]["plans"][0]["plan_id"]
                started = time.perf_counter()
                feedback = (
                    (
                        "Please replace only transform with an execution-local function named revision_transform. "
                        "Add 1 to each supplied value before dividing by 2, then compute the sum using repair_finish. "
                        "Registered repair_transform and registered_transform cannot perform the +1 adjustment. "
                        "Use the supplied in-memory values, no other data. All parameters are fixed; no clarification needed."
                    )
                    if args.real
                    else "모든 후보가 마음에 안 들어. 다른 변환 방법으로 해줘"
                )
                await submit_action(
                    run,
                    {
                        "action": "replan",
                        "interaction_id": form["interaction_id"],
                        "revision": form["revision"],
                        "feedback": feedback,
                    },
                )
                run = await wait()
                review_count = 0
                question_count = 0
                async with asyncio.timeout(240):
                    while run["status"] not in {
                        "success",
                        "error",
                        "timeout",
                        "canceled",
                        "recovery_required",
                    }:
                        if run["status"] == "waiting_input":
                            form = run["interrupt"][0]
                            if form["kind"] == "planning_question":
                                question_count += 1
                                await submit_action(
                                    run,
                                    {
                                        "action": "answer_clarification",
                                        "interaction_id": form[
                                            "interaction_id"
                                        ],
                                        "revision": form["revision"],
                                        "feedback": "각 값은 2로 나누고 합계를 내주세요.",
                                    },
                                )
                            else:
                                assert form["kind"] == "plan_review", run
                                plan = form["payload"]["plans"][0]
                                if plan["plan_id"] == old_plan:
                                    async with (
                                        graph_runtime.open_graph() as graph
                                    ):
                                        checkpoint = await graph.aget_state(
                                            {
                                                "configurable": {
                                                    "thread_id": build_langgraph_thread_id(
                                                        sid
                                                    )
                                                }
                                            }
                                        )
                                    record["diagnostic_validation_error"] = (
                                        checkpoint.values.get(
                                            "planning_validation_error"
                                        )
                                    )
                                    raise AssertionError(
                                        "Revision produced no validated new candidate; original plan was preserved, not submitted"
                                    )
                                review_count += 1
                                await submit_action(
                                    run,
                                    {
                                        "action": "approve_plan",
                                        "plan_id": plan["plan_id"],
                                        "plan_revision": plan["plan_revision"],
                                    },
                                )
                        await asyncio.sleep(0.2)
                        run = await read()
                if not run.get("result"):
                    record.update(
                        public_status=run["status"],
                        public_failure=run.get("failure"),
                    )
                    raise AssertionError(
                        "Agent failed before a validated revision/Executor result"
                    )
                final = run["result"]["final_response"]
                assert (
                    run["status"] == "success"
                    and final["status"] == "analysis_completed"
                ), run
                async with graph_runtime.open_graph() as graph:
                    snapshot = await graph.aget_state(
                        {
                            "configurable": {
                                "thread_id": build_langgraph_thread_id(sid)
                            }
                        }
                    )
                state = snapshot.values
                approved = state["approved_snapshot"]
                assert state["terminal_event_seen"] and not snapshot.next
                assert state["executor_operation_number"] == 1
                assert (
                    sum(o["step_id"] == "load" for o in state["observations"])
                    == 1
                )
                if args.real:
                    assert approved.get(
                        "execution_kind"
                    ) == "free_code" and not approved.get("workflow_eligible")
                    assert (
                        final["observations"][-1]["summary"]["items"]["sum"]
                        == 7.5
                    )
                if not args.real:
                    assert final["observations"][-1]["summary"]["items"][
                        "sum"
                    ] == (4 if scenario == "modified" else 6)
                    assert approved["workflow_eligible"] == (
                        scenario == "registered"
                    )
                    assert review_count == (
                        0
                        if args.no_approval and scenario != "registered"
                        else 1
                    )
                    assert question_count == (
                        1 if scenario == "clarification" else 0
                    )
                response = await client.get(
                    path + "/" + rid + "/stream", headers=headers
                )
                assert (
                    "def revision_transform" not in response.text
                    and "code_sha256" not in response.text
                )
                record.update(
                    passed=True,
                    seconds=round(time.perf_counter() - started, 3),
                    operation_count=state["executor_operation_number"],
                    planning_revision_count=state["planning_revision_count"],
                    review_count=review_count,
                    question_count=question_count,
                    execution_kind=approved.get("execution_kind"),
                    approval_mode=approved.get("approval_mode"),
                    workflow_eligible=approved.get("workflow_eligible"),
                    terminal_event_seen=state["terminal_event_seen"],
                    final=final,
                )
                print(
                    json.dumps(
                        {
                            k: record[k]
                            for k in [
                                "scenario",
                                "passed",
                                "seconds",
                                "execution_kind",
                                "approval_mode",
                                "review_count",
                                "question_count",
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        summary["passed"] = True
    except BaseException as exc:
        summary.update(error_type=type(exc).__name__, error=str(exc)[:3500])
        raise
    finally:
        server.should_exit = True
        await task
        graph_runtime._load_graph_inputs = original_inputs
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_private_result(args.output, summary)


asyncio.run(main())

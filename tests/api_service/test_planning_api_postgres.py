"""Opt-in real API/Worker/PostgreSQL checkpoints; never submits Executor code."""

from dtest.application.runs import monitoring
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import dtest.settings.loader as service_settings
import dtest.infrastructure.database.runtime as database
import dtest.worker_service.command_worker as worker
import dtest.application.runs.execution as runs
import dtest.application.runs.tasks as tasks
import dtest.application.runs.token_events as tokens
from dtest.application.runs.runtime import runtime as graph_runtime
from dtest.infrastructure.database.runtime import get_db
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.models.message_model import MessageModel
from dtest.infrastructure.database.models.task_event_model import (
    TaskEventModel,
)
from tests.api_service.test_user_identity_postgres import (
    initialize,
    add_user,
    add_session,
    headers,
)
from dtest.bootstrap import create_app
from dtest.contracts.plan_interaction import InteractionEvent
from dtest.contracts.run_events import RunEvent

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def test_config(tmp_path_factory):
    file = os.getenv("DTEST_AGENTIC_TEST_SETTINGS_FILE")
    if not file:
        pytest.skip(
            "Needs isolated local agentic_runtime_test + "
            "agentic_checkpoint_test "
            "DBs"
        )
    settings = json.loads(Path(file).read_text())
    crud, checkpoint = (
        make_url(settings["database_url"]),
        make_url(settings["checkpoint_db_uri"]),
    )
    assert (
        crud.database == "agentic_runtime_test"
        and checkpoint.database == "agentic_checkpoint_test"
    )
    assert crud.host in {"127.0.0.1", "localhost"} and checkpoint.host in {
        "127.0.0.1",
        "localhost",
    }
    path = tmp_path_factory.mktemp("agentic-migrations") / "config.yml"
    import yaml

    path.write_text(yaml.safe_dump(settings))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.crud.ini",
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "SERVICE_CONFIG_FILE": str(path),
            "PYTHONPATH": str(ROOT / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return settings


@pytest_asyncio.fixture
async def planning(test_config, monkeypatch):
    engine = create_async_engine(
        test_config["database_url"], poolclass=NullPool
    )
    factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )
    async with engine.begin() as db:
        await db.execute(text("TRUNCATE users CASCADE"))
    monkeypatch.setattr(service_settings, "_snapshot", None)
    settings = service_settings.load_settings(
        config={
            **test_config,
            "AGENT_WORKER_ENABLED": False,
            "TASK_RECONCILER_ENABLED": False,
            "EVENT_WORKER_ENABLED": False,
            "EXECUTOR_SUBMIT_ENABLED": False,
            "MODEL_PROVIDER": "mock",
            "CHECKPOINT_SETUP_ON_START": True,
            "TASK_CANCEL_POLL_INTERVAL_SECONDS": 0.2,
            "AGENT_WORKER_POLL_INTERVAL_SECONDS": 0.1,
            "ANALYSIS_DATASETS": {
                "default-nce": {
                    "title": "NCE",
                    "scope": "GLOBAL",
                    "runtime_path": (
                        "/workspace/pv/default_data/df_nce_long_format.parquet"
                    ),
                }
            },
        },
        environ={},
    )
    from dtest.infrastructure.workflow_search import (
        runtime as workflow_runtime,
    )

    monkeypatch.setattr(workflow_runtime, "_runtime", None)
    app = create_app(settings)
    for module in (database, worker, runs, tasks, tokens, monitoring):
        monkeypatch.setattr(module, "get_session_factory", lambda: factory)

    async def request_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    from tests.api_service.auth_double import install_business_identity_double

    install_business_identity_double(app, factory)
    from dtest.infrastructure.memory.store import runtime as store_runtime

    store_runtime.start()
    graph_runtime.start()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = SimpleNamespace(client=client, factory=factory, app=app)
        await initialize(h)
        h.user = await add_user(h)
        h.session_id = await add_session(h, h.user)
        h.path = f"/api/v1/sessions/{h.session_id}/runs"
        yield h
    await app.state.run_stream_hub.close()
    await graph_runtime.shutdown()
    await store_runtime.shutdown()
    await workflow_runtime.close_workflow_runtime()
    await engine.dispose()


async def submit(h, body, key=None):
    return await h.client.post(
        h.path,
        headers={
            **headers(h.user["user_id"]),
            "Idempotency-Key": key or str(uuid4()),
        },
        json=body,
    )


async def execute():
    claimed = await worker.claim_one()
    assert claimed is not None
    await worker.execute_claimed(claimed)


async def read(h, run_id):
    response = await h.client.get(
        h.path + "/" + run_id, headers=headers(h.user["user_id"])
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_api_edit_restart_approval_replay_stream_and_private_snapshot(
    planning,
):
    h = planning
    response = await submit(
        h,
        {
            "input": {
                "content": [
                    {"type": "text", "text": "품질과 이상치를 분석해줘"}
                ]
            }
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    await execute()
    waiting = await read(h, run_id)
    assert waiting["status"] == "waiting_input", waiting
    plan = waiting["interrupt"][0]["payload"]["plans"][0]
    token = waiting["resume_token"]
    action = {
        "action": "edit_plan",
        "plan_id": plan["plan_id"],
        "plan_revision": 1,
        "excluded_step_ids": ["outliers"],
        "input_values": {"dataset": "default-nce"},
        "step_changes": [
            {
                "step_id": "statistics",
                "parameter": "columns",
                "value": ["max_val"],
            }
        ],
    }
    body = {
        "run_id": run_id,
        "resume_token": token,
        "command": {"resume": action},
    }
    bad = {
        **body,
        "command": {"resume": {**action, "excluded_step_ids": ["load"]}},
    }
    assert (await submit(h, bad)).status_code == 422
    assert (await read(h, run_id))["resume_token"] == token
    bad = {
        **body,
        "command": {
            "resume": {
                **action,
                "step_changes": [
                    {
                        "step_id": "statistics",
                        "parameter": "columns",
                        "value": "invalid",
                    }
                ],
            }
        },
    }
    assert (await submit(h, bad)).status_code == 422
    assert (await read(h, run_id))["resume_token"] == token
    assert (await submit(h, body, key="edit-once")).status_code == 202
    await execute()
    edited = await read(h, run_id)
    assert (
        edited["status"] == "waiting_input" and edited["resume_token"] != token
    )
    assert edited["interrupt"][0]["payload"]["plans"][0]["plan_revision"] == 2
    assert (await submit(h, body, key="edit-once")).status_code == 202
    assert (await submit(h, body, key="old-screen")).status_code == 409
    await graph_runtime.shutdown()
    graph_runtime.start()
    approval = {
        "run_id": run_id,
        "resume_token": edited["resume_token"],
        "command": {
            "resume": {
                "action": "approve_plan",
                "plan_id": plan["plan_id"],
                "plan_revision": 2,
            }
        },
    }
    assert (await submit(h, approval, key="approve-once")).status_code == 202
    await execute()
    final = await read(h, run_id)
    assert final["status"] == "success", final
    assert final["result"]["final_response"]["status"] == "plan_approved"
    assert "code" not in json.dumps(final) and "/workspace" not in json.dumps(
        final
    )
    assert (await submit(h, approval, key="approve-once")).status_code == 202
    async with h.factory() as db:
        rows = list(
            await db.scalars(
                select(AgentRunModel).order_by(AgentRunModel.created_at)
            )
        )
        assert len(rows) == 3
        frozen = rows[-1].metadata_json["_approved_plan"]
        assert frozen["approval_sha256"] and len(frozen["steps"]) == 3
        assert frozen["steps"][2]["arguments"]["columns"]["value"] == [
            "max_val"
        ]
        assert frozen["dataset_bindings"]["dataset"][
            "runtime_path"
        ].startswith("/workspace/pv/default_data/")
        assert all(item["code"] for item in frozen["tool_sources"].values())
        assert (
            await db.scalar(select(func.count()).select_from(MessageModel))
            == 2
        )
        events = list(
            await db.scalars(
                select(TaskEventModel).order_by(TaskEventModel.sequence)
            )
        )
        interactions = [
            event
            for event in events
            if event.event_type
            in {"interaction.opened", "interaction.updated"}
        ]
        assert len(interactions) == 2
        for event in interactions:
            InteractionEvent.model_validate(event.payload)
    stream = await h.client.get(
        h.path + "/" + run_id + "/stream", headers=headers(h.user["user_id"])
    )
    assert stream.status_code == 200
    assert (
        "event: interaction.resolved" in stream.text
        and "event: run.snapshot" in stream.text
    )
    assert '"code"' not in stream.text and "llm.token" not in stream.text
    packets = [
        json.loads(line[6:])
        for line in stream.text.splitlines()
        if line.startswith("data: ") and '"sequence":' in line
    ]
    assert packets and all(
        RunEvent.model_validate(packet) for packet in packets
    )
    last = packets[-1]["sequence"]
    replay = await h.client.get(
        h.path + "/" + run_id + "/stream",
        headers={**headers(h.user["user_id"]), "Last-Event-ID": str(last)},
    )
    assert "id: " not in replay.text
    # POST stream replays the original admission, does not create another invocation.
    streamed = await h.client.post(
        h.path + "/stream",
        headers={
            **headers(h.user["user_id"]),
            "Idempotency-Key": "approve-once",
        },
        json=approval,
    )
    assert (
        streamed.status_code == 200 and streamed.headers["x-run-id"] == run_id
    )
    assert "event: interaction.resolved" in streamed.text


@pytest.mark.asyncio
async def test_answer_unknown_model_invalid_attachments_and_old_route(
    planning,
):
    h = planning
    body = {
        "input": {
            "content": [{"type": "text", "text": "[answer] EDA가 뭐야?"}]
        }
    }
    assert (
        await submit(h, {**body, "main_model_name": "missing"})
    ).status_code == 422
    assert (
        await submit(
            h,
            {
                "input": {
                    "content": [{"type": "file", "file_id": str(uuid4())}]
                }
            },
        )
    ).status_code == 422
    started = await submit(h, body)
    assert started.status_code == 202
    await execute()
    final = await read(h, started.json()["run_id"])
    assert (
        final["status"] == "success"
        and final["result"]["final_response"]["status"] == "answer"
    )
    removed = await h.client.post(
        h.path + "/" + final["run_id"] + "/resume",
        headers=headers(h.user["user_id"]),
        json={},
    )
    assert removed.status_code == 404


@pytest.mark.asyncio
async def test_session_kernel_is_pinned_through_worker_hitl_restart_and_approval(
    planning, monkeypatch
):
    from dataclasses import replace

    h = planning
    settings = service_settings.get_settings()
    monkeypatch.setattr(
        service_settings,
        "_snapshot",
        replace(
            settings,
            agent=replace(
                settings.agent,
                executor_runtime_profiles=(
                    settings.agent.executor_runtime_profile,
                    "3102311",
                ),
            ),
        ),
    )
    response = await h.client.post(
        f"/api/v1/projects/{h.user['default_project_id']}/sessions",
        headers=headers(h.user["user_id"]),
        json={"settings": {"kernel_profile": "3102311"}},
    )
    assert response.status_code == 201, response.text
    h.session_id = response.json()["id"]
    h.path = f"/api/v1/sessions/{h.session_id}/runs"
    started = await submit(
        h,
        {"input": {"content": [{"type": "text", "text": "품질을 분석해줘"}]}},
    )
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    await execute()
    waiting = await read(h, run_id)
    assert waiting["status"] == "waiting_input", waiting
    plan = waiting["interrupt"][0]["payload"]["plans"][0]
    # A new process/default/creation allowlist must not change this HITL run's kernel.
    monkeypatch.setattr(service_settings, "_snapshot", settings)
    await graph_runtime.shutdown()
    graph_runtime.start()
    approved = await submit(
        h,
        {
            "run_id": run_id,
            "resume_token": waiting["resume_token"],
            "command": {
                "resume": {
                    "action": "approve_plan",
                    "plan_id": plan["plan_id"],
                    "plan_revision": plan["plan_revision"],
                }
            },
        },
    )
    assert approved.status_code == 202, approved.text
    await execute()
    final = await read(h, run_id)
    assert final["status"] == "success", final
    async with h.factory() as db:
        invocation = await db.scalar(
            select(AgentRunModel)
            .where(AgentRunModel.public_run_id == UUID(run_id))
            .order_by(
                AgentRunModel.created_at.desc(), AgentRunModel.run_id.desc()
            )
            .limit(1)
        )
        assert (
            invocation.metadata_json["_approved_plan"]["context"][
                "kernel_profile"
            ]
            == "3102311"
        )

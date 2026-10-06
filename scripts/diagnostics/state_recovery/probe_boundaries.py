"""Characterize current failure windows; passing reproductions are NOT correctness tests.

Run explicitly with a disposable local identity_test PostgreSQL. These probes
assert the observed defects at review commit 9ebbc20. Run that commit to
reproduce the original defects; later fixes intentionally invalidate these assertions.
No production logic, LLM service or Executor service is changed/contacted.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, TypedDict
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

import dtest.settings.loader as service_settings
import dtest.worker_service.command_worker as worker
import dtest.application.runs.execution as runs
import dtest.application.runs.runtime as graphs
import dtest.application.runs.persistence.graph as projection
from dtest.application.runs.lifecycle import execution_health
from dtest.contracts.enums import AgentRunStatus, TaskStatus
from dtest.infrastructure.database.models.agent_run_log_model import (
    AgentRunLogModel,
)
from dtest.infrastructure.database.models.task_event_model import (
    TaskEventModel,
)
from dtest.infrastructure.database.models.session_execution_model import (
    SessionExecutionModel,
)
from dtest.application.runs.logs import AgentRunLogService
from dtest.application.runs.task_events import TaskEventService
from dtest.application.runs.tasks import TaskService
from dtest.contracts.values import utc_now
from dtest.agent_service.runtime.langgraph.checkpointer import (
    create_checkpointer,
)
from tests.api_service.test_user_identity_postgres import (
    database_url,
    harness,
    headers,
)
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue, rows
from tests.api_service.test_public_run_postgres import state, resume, execute

pytestmark = pytest.mark.asyncio


def evidence(name, **values):
    directory = Path(
        os.environ.get(
            "DTEST_RECOVERY_EVIDENCE_DIR", "/tmp/dtest-recovery-evidence"
        )
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(
        json.dumps(values, indent=2, default=str) + "\n"
    )


class ReviewState(TypedDict, total=False):
    user_id: str
    project_id: str
    session_id: str
    run_id: str
    thread_id: str
    request_id: str
    user_request: str
    agent_run_id: str
    model_selection: dict
    project_system_prompt: str
    project_memory: Any
    task_id: str
    routing_result: dict
    answer1: Any
    answer2: Any


@asynccontextmanager
async def two_interrupts(h, monkeypatch):
    graph_task = str(uuid4())

    async def start(s):
        return {"routing_result": {"route": "analysis"}, "task_id": graph_task}

    async def one(s):
        return {"answer1": interrupt({"kind": "USER_APPROVAL", "stage": 1})}

    async def two(s):
        return {"answer2": interrupt({"kind": "USER_APPROVAL", "stage": 2})}

    def build(saver):
        b = StateGraph(ReviewState)
        for name, node in [("start", start), ("one", one), ("two", two)]:
            b.add_node(name, node)
        for left, right in [
            (START, "start"),
            ("start", "one"),
            ("one", "two"),
            ("two", END),
        ]:
            b.add_edge(left, right)
        return b.compile(checkpointer=saver)

    dsn = (
        make_url(service_settings.get_settings().database.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )

    @asynccontextmanager
    async def graph_context():
        async with create_checkpointer(
            database_url=dsn,
            setup_on_start=True,
            min_size=1,
            max_size=2,
            timeout=2,
        ) as saver:
            yield build(saver)

    rt = graphs.AgentGraphRuntime()
    monkeypatch.setattr(rt, "_graph_context", graph_context)
    monkeypatch.setattr(graphs, "runtime", rt)
    try:
        yield rt
    finally:
        await rt.shutdown()


async def test_log_commit_then_event_failure_is_not_repaired_by_replay(
    runtime, monkeypatch
):
    h = runtime
    queued = await enqueue(h)
    rid = UUID(queued["run_id"])
    payload = dict(
        run_id=rid,
        event_key="review-event",
        agent_name="review",
        node="node",
        event="result",
        kind="result",
        payload={"synthetic": True},
    )
    original = TaskEventService.append_for_run

    async def unavailable(*args, **kwargs):
        raise SQLAlchemyError("injected event write failure")

    monkeypatch.setattr(
        TaskEventService, "append_for_run", staticmethod(unavailable)
    )
    async with h.factory() as db:
        with pytest.raises(SQLAlchemyError):
            await AgentRunLogService.create(db, **payload)
    monkeypatch.setattr(
        TaskEventService, "append_for_run", staticmethod(original)
    )
    async with h.factory() as db:
        await AgentRunLogService.create(db, **payload)
    async with h.factory() as db:
        logs = await db.scalar(
            select(func.count())
            .select_from(AgentRunLogModel)
            .where(AgentRunLogModel.run_id == rid)
        )
        events = await db.scalar(
            select(func.count())
            .select_from(TaskEventModel)
            .where(
                TaskEventModel.run_id == rid,
                TaskEventModel.event_type == "agent.event",
            )
        )
    assert (logs, events) == (1, 0)
    evidence(
        "log_event_gap",
        log_rows=logs,
        agent_events_after_same_key_replay=events,
    )


async def test_projection_failure_retries_consumed_resume_on_next_interrupt(
    runtime, monkeypatch
):
    h = runtime
    current = service_settings.get_settings()
    monkeypatch.setattr(
        service_settings,
        "_snapshot",
        replace(
            current,
            api=current.api.model_copy(update={"agent_worker_max_retries": 1}),
        ),
    )
    async with two_interrupts(h, monkeypatch) as rt:
        queued = await enqueue(h)
        await execute()
        first = await state(h, queued["run_id"])
        assert first["status"] == "waiting_input"
        command = {"approved": True, "marker": "first-question-only"}
        assert (await resume(h, first, command=command)).status_code == 202
        persist = projection._persist_state
        failed = False

        async def fail_after_checkpoint(value, **kwargs):
            nonlocal failed
            if "answer1" in value and "answer2" not in value and not failed:
                failed = True
                raise SQLAlchemyError(
                    "injected projection failure after durable next interrupt"
                )
            return await persist(value, **kwargs)

        monkeypatch.setattr(
            projection, "_persist_state", fail_after_checkpoint
        )
        await execute()
        run, task = await rows(h, queued["run_id"])
        assert (
            run.status == AgentRunStatus.PENDING
            and run.failure["retry_scheduled"]
        )
        async with rt.open_graph() as graph:
            middle = await graph.aget_state(
                graphs.graph_config(h.session_id, queued["run_id"])
            )
            assert (
                middle.values["answer1"] == command
                and "answer2" not in middle.values
            )
            assert middle.tasks[0].interrupts[0].value["stage"] == 2
        await execute()
        async with rt.open_graph() as graph:
            final = await graph.aget_state(
                graphs.graph_config(h.session_id, queued["run_id"])
            )
        run, task = await rows(h, queued["run_id"])
        assert final.values["answer1"] == final.values["answer2"] == command
        assert run.status == AgentRunStatus.SUCCESS and run.attempt_count == 2
        evidence(
            "resume_replay_crosses_interrupt",
            user_resume_requests=1,
            automatic_attempts=run.attempt_count,
            answer1=final.values["answer1"],
            answer2=final.values["answer2"],
            final_run_status=run.status.value,
            intermediate_checkpoint_wait_stage=2,
        )


async def test_final_projection_error_leaves_running_until_stale_reconciler(
    runtime, monkeypatch
):
    h = runtime
    async with two_interrupts(h, monkeypatch) as rt:
        queued = await enqueue(h)
        original = TaskEventService.append_for_run

        async def fail_final(*args, **kwargs):
            if kwargs.get("event_type") == "task.waiting_input":
                raise SQLAlchemyError("injected final state event failure")
            return await original(*args, **kwargs)

        monkeypatch.setattr(
            TaskEventService, "append_for_run", staticmethod(fail_final)
        )
        await execute()
        run, task = await rows(h, queued["run_id"])
        public = await state(h, queued["run_id"])
        async with rt.open_graph() as graph:
            saved = await graph.aget_state(
                graphs.graph_config(h.session_id, queued["run_id"])
            )
        async with h.factory() as db:
            owner = await db.get(SessionExecutionModel, UUID(h.session_id))
            owner_released = owner.token is None
        assert saved.tasks[0].interrupts[0].value["stage"] == 1
        assert (
            run.status == AgentRunStatus.RUNNING
            and task.status == TaskStatus.RUNNING
        )
        assert public["status"] == "running" and not task.recovery_required
        assert (
            owner_released
            and execution_health.healthy
            and await worker.claim_one() is None
        )
        async with h.factory() as db:
            affected = await TaskService.reconcile_stale(
                db, now=utc_now() + timedelta(days=1)
            )
        _, after = await rows(h, queued["run_id"])
        assert after.recovery_required and len(affected) == 1
        evidence(
            "final_projection_gap",
            checkpoint="input_wait_stage_1",
            api_before_reconciler=public["status"],
            worker_claimable=False,
            process_healthy=True,
            session_execution_owner_released=owner_released,
            reconciler_marks_recovery=True,
            reconciler_requeues=False,
        )


CHILD = """
import asyncio,json,os,sys
from uuid import UUID,uuid4
import dtest.settings.loader as service_settings
settings=json.loads(sys.stdin.read())
service_settings.configure(service_settings.load_settings(config=settings,environ={}))
async def main():
 import dtest.worker_service.command_worker as worker
 if os.environ['REVIEW_KIND']=='api_run':
  item=await worker.claim_one()
  assert item is not None
 else:
  from dtest.infrastructure.database.runtime import get_session_factory
  from dtest.application.runs.ownership import SessionExecution,acquire
  async with get_session_factory()() as db:
   assert await acquire(db,SessionExecution(UUID(os.environ['REVIEW_SESSION']),uuid4(),uuid4(),'executor_event'))
   await db.commit()
 print('CLAIM_COMMITTED',flush=True)
 os._exit(17)
asyncio.run(main())
"""


@pytest.mark.parametrize("kind", ["api_run", "executor_event"])
async def test_hard_process_exit_preserves_owner_without_automatic_requeue(
    runtime, monkeypatch, kind
):
    h = runtime
    queued = await enqueue(h)
    if kind == "executor_event":
        monkeypatch.setattr(
            runs,
            "ainvoke_user_turn",
            AsyncMock(
                return_value={
                    "routing_result": {"route": "analysis"},
                    "__interrupt__": [
                        SimpleNamespace(value={"kind": "EXECUTOR_EVENT"})
                    ],
                }
            ),
        )
        await execute()
    settings = {
        "DATABASE_URL": service_settings.get_settings().database.database_url,
        "MODEL_PROVIDER": "mock",
        "EVENT_WORKER_ENABLED": False,
        "TASK_RECONCILER_ENABLED": False,
        "EXECUTOR_SUBMIT_ENABLED": False,
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "HOME", "TMPDIR", "LANG")
    }
    env.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[3] / "src"),
        REVIEW_KIND=kind,
        REVIEW_SESSION=h.session_id,
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", CHILD],
        input=json.dumps(settings),
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 17 and "CLAIM_COMMITTED" in result.stdout, (
        result.stderr
    )
    async with h.factory() as db:
        affected = await TaskService.reconcile_stale(
            db, now=utc_now() + timedelta(days=1)
        )
    run, task = await rows(h, queued["run_id"])
    async with h.factory() as db:
        owner = await db.get(SessionExecutionModel, UUID(h.session_id))
    assert owner.token is not None and not owner.recovery_required
    assert await worker.claim_one() is None
    assert len(affected) == (1 if kind == "api_run" else 0)
    assert task.recovery_required == (kind == "api_run")
    public = await state(h, queued["run_id"])
    assert public["status"] == (
        "recovery_required" if kind == "api_run" else "waiting_executor"
    )
    evidence(
        f"process_exit_{kind}",
        child_exit_code=17,
        durable_owner_retained=True,
        owner_recovery_flag=owner.recovery_required,
        task_status=task.status.value,
        task_recovery_flag=task.recovery_required,
        reconciler_affected=len(affected),
        api_status_after_reconciler=public["status"],
        automatic_requeue=False,
    )

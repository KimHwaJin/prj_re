"""Compare checkpoint payload and followup cleanup with registered Tool fixtures.

This is a functional/storage probe, not an HTTP, model-latency or throughput test.
The reference variant loads only the graph/nodes at a pinned Git revision in this
isolated process. No checkout is switched. Real PG is opt-in and must be a local,
disposable agentic_checkpoint_test DB; existing threads are not deleted.
"""

import argparse
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts/benchmarks/worker_e2e"))


def reference_sources(revision):
    resolved = subprocess.check_output(
        ["git", "rev-parse", "--verify", revision + "^{commit}"],
        cwd=ROOT,
        text=True,
    ).strip()
    digests = {}
    for name, file in (
        (
            "dtest.agent_service.agents.analysis.execution.nodes",
            "src/dtest.agent_service/agents/analysis/execution/nodes.py",
        ),
        (
            "dtest.agent_service.agents.analysis.planning.graph",
            "src/dtest.agent_service/agents/analysis/planning/graph.py",
        ),
    ):
        importlib.import_module(name.rsplit(".", 1)[0])
        source = subprocess.check_output(
            ["git", "show", resolved + ":" + file], cwd=ROOT
        )
        module = types.ModuleType(name)
        module.__package__ = name.rsplit(".", 1)[0]
        module.__file__ = str(ROOT / file)
        sys.modules[name] = module
        exec(compile(source, str(ROOT / file), "exec"), module.__dict__)
        digests[file] = hashlib.sha256(source).hexdigest()
    return resolved, digests


def state_evidence(state):
    command = state.get("execution_command", {})
    return {
        "task_id": state["task_id"],
        "run_id": state["public_run_id"],
        "final_status": state["final_response"]["status"],
        "command_json_bytes": len(
            json.dumps(
                command, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ),
        "command_has_source": bool(command),
        "operation_id_present": bool(state.get("executor_operation_id")),
        "execution_phase": state.get("execution_phase"),
        "execution_error_present": bool(state.get("execution_error")),
        "history_messages": len(state["history"]),
        "completed_analysis_kept": bool(state.get("last_analysis_context")),
        "latest_approved_source_kept": bool(state.get("approved_snapshot")),
        "initial_receipt_matches": state.get(
            "initial_request_receipt", {}
        ).get("command_id")
        == state["run_id"],
    }


async def profile(args, dsn):
    import pytest
    from dtest.agent_service.runtime.langgraph.checkpointer import (
        create_checkpointer,
    )
    from tests.agent_service import test_agentic_execution as fixture
    from checkpoint_profile import capture

    with pytest.MonkeyPatch.context() as patch:
        async with create_checkpointer(
            dsn, setup_on_start=True, min_size=1, max_size=2
        ) as saver:
            patch.setattr(fixture, "InMemorySaver", lambda: saver)
            (
                runtime,
                executor,
                graph,
                config,
                state,
                deliver,
            ) = await fixture.setup(args.fixture_dir, patch)
            _, state = await deliver(executor.events[0])
            _, state = await deliver(executor.events[1])
            _, state = await deliver(
                executor.event(
                    "execution.completed",
                    {"status": "SUCCEEDED", "error": None},
                )
            )
            stages = []

            async def save_stage(name, state):
                stored = await asyncio.to_thread(
                    capture, dsn, [config["configurable"]["thread_id"]]
                )
                cp = stored["checkpoints"][-1]
                latest = {
                    (b["channel"], b["version"]): b["bytes"]
                    for b in stored["blobs"]
                }
                latest_bytes = (
                    cp["checkpoint_json_bytes"]
                    + cp["metadata_json_bytes"]
                    + sum(
                        latest.get((channel, version), 0)
                        for channel, version in cp["versions"].items()
                    )
                )
                total_bytes = (
                    sum(
                        r["checkpoint_json_bytes"] + r["metadata_json_bytes"]
                        for r in stored["checkpoints"]
                    )
                    + sum(r["bytes"] for r in stored["blobs"])
                    + sum(r["bytes"] for r in stored["writes"])
                )
                actual = await saver.aget_tuple(config)
                stages.append(
                    {
                        "stage": name,
                        "state": state_evidence(state),
                        "rows": stored,
                        "checkpoint_count": len(stored["checkpoints"]),
                        "logical_payload_bytes": total_bytes,
                        "latest_json_and_referenced_blobs_bytes": latest_bytes,
                        "latest_versions_seen_keys": {
                            name: len(fields)
                            for name, fields in actual.checkpoint[
                                "versions_seen"
                            ].items()
                        },
                    }
                )

            await save_stage("analysis_completed", state)
            semantic = {
                "status": state["final_response"]["status"],
                "completed_steps": state["completed_steps"],
                "decisions": state["execution_decisions"],
                "skipped_steps": state["skipped_steps"],
                "observations": [
                    {
                        k: o[k]
                        for k in ("step_id", "tool_id", "status", "summary")
                    }
                    for o in state["final_response"]["observations"]
                ],
                "report_status": state["report_status"],
            }
            calls = len(executor.calls)
            for index in (1, 2):
                run_id = str(uuid4())
                state = await graph.ainvoke(
                    {
                        **{
                            k: state[k]
                            for k in (
                                "user_id",
                                "project_id",
                                "session_id",
                                "model_selection",
                            )
                        },
                        "run_id": run_id,
                        "initial_request_identity": {"command_id": run_id},
                        "user_request": (
                            "[answer] 방금 실제 분석 결과를 설명해줘"
                        ),
                    },
                    config,
                    durability="sync",
                )
                assert (
                    state["final_response"]["status"] == "answer"
                    and state["last_analysis_context"]
                )
                assert len(executor.calls) == calls
                await save_stage("followup_" + str(index), state)
            assert (
                saver.conn.get_stats()["pool_available"]
                == saver.conn.get_stats()["pool_size"]
            )
            reads = {
                name: len(spec.input_schema.__annotations__)
                for name, spec in graph.builder.nodes.items()
            }
    return {
        "variant": args.variant,
        "scope": (
            "one registered-tool 6-row MULTI analysis plus two FAQ "
            "turns; real PG, in-process model/Executor doubles; no "
            "wall-time "
            "claim"
        ),
        "stages": stages,
        "semantic_outcome": semantic,
        "executor_http_double_calls": calls,
        "node_input_field_counts": reads,
        "latest_size_definition": (
            "checkpoint JSON + metadata JSON + referenced version "
            "blobs; excludes pending writes and Python "
            "heap"
        ),
        "total_size_definition": (
            "checkpoint JSON + metadata JSON + unique blobs + writes; "
            "excludes row keys/index/WAL/physical "
            "pages"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant", choices=["reference", "candidate"], required=True
    )
    parser.add_argument("--reference-revision", default="2622552")
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ["DTEST_STATE_PROFILE_DSN"]
    info = conninfo_to_dict(dsn)
    assert (
        info.get("host") in {"localhost", "127.0.0.1"}
        and info.get("dbname") == "agentic_checkpoint_test"
    )
    assert not args.output.exists() and not args.fixture_dir.exists()
    args.fixture_dir.mkdir(parents=True)
    if args.variant == "reference":
        commit, digests = reference_sources(args.reference_revision)
    else:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        paths = [
            "src/dtest.agent_service/agents/analysis/planning/graph.py",
            "src/dtest.agent_service/agents/analysis/execution/nodes.py",
            "src/dtest.agent_service/agents/analysis/state.py",
            "src/dtest.agent_service/agents/analysis/planning/lifecycle.py",
        ]
        digests = {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in paths
        }
    result = asyncio.run(profile(args, dsn))
    result["source"] = {
        "parent_or_reference_commit": commit,
        "runtime_files_sha256": digests,
        "candidate_uses_working_tree": args.variant == "candidate",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "variant": args.variant,
                "stages": [
                    {
                        k: s[k]
                        for k in (
                            "stage",
                            "checkpoint_count",
                            "logical_payload_bytes",
                            "latest_json_and_referenced_blobs_bytes",
                        )
                    }
                    for s in result["stages"]
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

"""Loopback HTTP Executor fixture. NEVER imports or executes submitted Python.

Strict request contracts, content snapshots, SHA256 manifests, contiguous Redis
history and MULTI optimistic versions run for real. Outputs are labelled fixtures.
This is deliberately absent from production entrypoints and service settings.
"""

import argparse, ast, asyncio, hashlib, json, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from dtest.contracts.executor import (
    ExecutorRequestBody,
    ExecutorContinueRequestBody,
    ExecutorFinalizeRequestBody,
)
from dtest.contracts.events import ExecutorEvent


def logical_step(code):
    """Read the compiler's observation literal via AST; no eval/exec/import."""
    matches = []
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Dict):
            values = {
                k.value: v
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant)
            }
            value = values.get("step_id")
            if (
                "summary" in values
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                matches.append(value.value)
    if len(matches) != 1:
        raise ValueError("Expected one compiler observation step literal")
    return matches[0]


def write_results(root, execution_id, operation_id, steps, log_bytes=0):
    """Real file I/O/checksums; synthetic small observations, no data analysis."""
    assert log_bytes in (0, 65536), "Bounded synthetic diagnostic output only"
    results = []
    for step in steps:
        step_id, attempt_id = step["step_id"], str(uuid4())
        sequence, source = (
            step["sequence"],
            step["payload"]["source"]["content"],
        )
        logical = logical_step(source)
        prefix = f"executions/{execution_id}/operations/{operation_id}/steps/{step_id}"
        folder = root / prefix
        folder.mkdir(parents=True, exist_ok=False)
        source_bytes = source.encode()
        (folder / "source.py").write_bytes(source_bytes)
        summary = {
            "type": "service_fixture",
            "label": "SYNTHETIC: no Tool code executed",
            "shape": [6, 1],
            "tool": step["lineage"]["tool_name"],
        }
        output = (
            ("L" * log_bytes + "\n" if log_bytes else "")
            + "DTEST_OBSERVATION "
            + json.dumps({"step_id": logical, "summary": summary})
            + "\n"
        ).encode()
        (folder / "stdout.txt").write_bytes(output)
        created = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema_version": "1.0",
            "state": "FINALIZED",
            "complete": True,
            "identity": {
                "execution_id": execution_id,
                "operation_id": operation_id,
                "step_id": step_id,
                "sequence": sequence,
                "execution_attempt_id": attempt_id,
                "fencing_token": 1,
            },
            "source": {
                "relative_path": prefix + "/source.py",
                "checksum_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "size_bytes": len(source_bytes),
            },
            "outputs": [
                {
                    "ordinal": 0,
                    "kind": "STREAM",
                    "stream_name": "stdout",
                    "execution_count": sequence + 1,
                    "representations": [
                        {
                            "media_type": "text/plain",
                            "encoding": "UTF8",
                            "relative_path": prefix + "/stdout.txt",
                            "size_bytes": len(output),
                            "checksum_sha256": hashlib.sha256(
                                output
                            ).hexdigest(),
                            "complete": True,
                            "truncated_in_preview": False,
                            "metadata": {},
                        }
                    ],
                    "metadata": {},
                    "created_at": created,
                }
            ],
            "output_count": 1,
            "representation_count": 1,
            "total_size_bytes": len(output),
            "execution_count": sequence + 1,
            "error_message": None,
            "output_summary": {
                "output_count": 1,
                "output_types": {"STREAM": 1},
                "stream_names": ["stdout"],
                "mime_types": ["text/plain"],
                "has_image": False,
                "image_count": 0,
                "has_error": False,
            },
            "created_at": created,
            "updated_at": created,
            "completed_at": created,
        }
        raw = json.dumps(manifest).encode()
        (folder / "manifest.json").write_bytes(raw)
        results.append(
            {
                "sequence": sequence,
                "step_id": step_id,
                "status": "SUCCEEDED",
                "attempt": {
                    "id": attempt_id,
                    "number": 1,
                    "reason": "INITIAL",
                },
                "error": None,
                "result_ref": {
                    "storage": "SHARED_PV",
                    "relative_path": prefix + "/manifest.json",
                    "size_bytes": len(raw),
                    "checksum_sha256": hashlib.sha256(raw).hexdigest(),
                    "complete": True,
                },
            }
        )
    return results


def create_app(cfg):
    root = Path(cfg["result_root"]).resolve()
    assert root.is_relative_to(Path("/private/tmp")) and root.name.startswith(
        "executor-fixture-"
    )
    broker = redis.from_url(cfg["redis_url"], decode_responses=True)
    executions, receipts, timeline, tasks = {}, {}, [], set()
    gate = asyncio.Event()
    gate.set()
    measure = {"active": False}
    failures = []
    history_requests = []

    async def publish(item, doc):
        await broker.xadd(
            cfg["stream"],
            {
                k: json.dumps(v) if isinstance(v, dict) else str(v)
                for k, v in doc.items()
            },
        )
        if measure["active"]:
            timeline.append(
                {
                    "stage": "event_published",
                    "at": time.perf_counter(),
                    "execution_id": item["id"],
                    "event_id": doc["event_id"],
                    "event_type": doc["event_type"],
                    "sequence": doc["event_sequence"],
                }
            )

    async def event(item, kind, payload):
        doc = ExecutorEvent(
            event_id=uuid4(),
            execution_id=item["id"],
            event_type=kind,
            event_sequence=len(item["events"]) + 1,
            schema_version="1.0",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        ).model_dump(mode="json")
        item["events"].append(doc)
        if cfg.get("reverse_event_batches") and kind in {
            "execution.operation_started",
            "execution.step_completed",
        }:
            item.setdefault("deferred_events", []).append(doc)
            return
        # History remains ordered. Only Redis delivery is reversed; no event is
        # deleted or renumbered, and the completed result exists before routing.
        batch = (
            [doc, *reversed(item.pop("deferred_events", []))]
            if kind == "execution.operation_completed"
            else [doc]
        )
        for outgoing in batch:
            await publish(item, outgoing)

    async def complete(item, operation):
        await gate.wait()
        await asyncio.sleep(cfg.get("delay_ms", 0) / 1000)
        await event(
            item,
            "execution.operation_started",
            {
                "operation": {
                    "id": operation["id"],
                    "number": operation["number"],
                }
            },
        )
        results = await asyncio.to_thread(
            write_results,
            root,
            item["id"],
            operation["id"],
            operation["steps"],
            cfg.get("log_bytes", 0),
        )
        for result in results:
            await event(
                item,
                "execution.step_completed",
                {
                    "step": {
                        "id": result["step_id"],
                        "sequence": result["sequence"],
                    },
                    "status": "SUCCEEDED",
                },
            )
        item["version"] += 2
        item["status"] = "WAITING_FOR_OPERATION"
        await event(
            item,
            "execution.operation_completed",
            {
                "operation": {
                    "id": operation["id"],
                    "number": operation["number"],
                },
                "step_results": results,
                "status": "SUCCEEDED",
                "execution_status": item["status"],
                "error": None,
                "continuation": {
                    "allowed": True,
                    "expected_version": item["version"],
                    "expires_at": "2100-01-01T00:00:00+00:00",
                },
            },
        )

    def spawn(awaitable):
        task = asyncio.create_task(awaitable)
        tasks.add(task)

        def settled(task):
            tasks.discard(task)
            if not task.cancelled() and task.exception():
                failures.append(type(task.exception()).__name__)

        task.add_done_callback(settled)

    def once(key, body):
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        previous = receipts.get(key)
        if previous and previous[0] != digest:
            raise HTTPException(409, "Idempotency conflict")
        return digest, previous[1] if previous else None

    def operation(item, spec):
        number = len(item["operations"]) + 1
        op = {
            "id": str(uuid4()),
            "number": number,
            "steps": [{**s, "step_id": str(uuid4())} for s in spec["steps"]],
        }
        item["operations"].append(op)
        receipt = {
            "execution_id": item["id"],
            "operation": {
                "operation_id": op["id"],
                "steps": [
                    {"sequence": s["sequence"], "step_id": s["step_id"]}
                    for s in op["steps"]
                ],
            },
            "state": {"status": "QUEUED", "version": item["version"]},
        }
        spawn(complete(item, op))
        return receipt

    def record(item, kind):
        if measure["active"]:
            timeline.append(
                {
                    "stage": kind,
                    "at": time.perf_counter(),
                    "execution_id": item["id"],
                }
            )

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await broker.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.get("/_bench/ready")
    async def ready():
        return {"ready": True}

    @app.post("/_bench/reset")
    async def reset():
        timeline.clear()
        failures.clear()
        history_requests.clear()
        measure["active"] = True
        return {"ok": True}

    @app.post("/_bench/gate/{action}")
    async def gating(action: str):
        if action == "hold":
            gate.clear()
        elif action == "release":
            gate.set()
        else:
            raise HTTPException(400, "Unknown gate action")
        return {"open": gate.is_set()}

    @app.get("/_bench/metrics")
    async def metrics():
        return {
            "timeline": timeline,
            "pending": len(tasks),
            "executions": [
                {
                    "execution_id": i["id"],
                    "context": i["context"],
                    "status": i["status"],
                    "operations": len(i["operations"]),
                    "events": len(i["events"]),
                }
                for i in executions.values()
            ],
            "tasks_failed": failures,
            "history_requests": history_requests,
        }

    @app.post("/api/v1/executions", status_code=202)
    async def submit(body: ExecutorRequestBody):
        data = body.model_dump(mode="json")
        digest, prior = once(("create", data["idempotency_key"]), data)
        if prior:
            return prior
        if data["lifecycle"]["operation_mode"] != "MULTI":
            raise HTTPException(422, "Fixture requires MULTI")
        item = {
            "id": str(uuid4()),
            "version": 1,
            "status": "QUEUED",
            "operations": [],
            "events": [],
            "context": data["context"],
        }
        executions[item["id"]] = item
        await event(item, "execution.started", {"status": "RUNNING"})
        response = operation(item, data["operation"]["spec"])
        receipts[("create", data["idempotency_key"])] = (digest, response)
        record(item, "submit_accepted")
        return response

    def find(eid):
        if eid not in executions:
            raise HTTPException(404, "Unknown execution")
        return executions[eid]

    @app.post("/api/v1/executions/{eid}/operations", status_code=202)
    async def continuation(eid: str, body: ExecutorContinueRequestBody):
        data = body.model_dump(mode="json")
        digest, prior = once((eid, "continue", data["idempotency_key"]), data)
        if prior:
            return prior
        item = find(eid)
        if (
            item["status"] != "WAITING_FOR_OPERATION"
            or item["version"] != data["expected_version"]
        ):
            raise HTTPException(409, "Version/state mismatch")
        item["status"] = "QUEUED"
        response = operation(item, data["spec"])
        receipts[(eid, "continue", data["idempotency_key"])] = (
            digest,
            response,
        )
        record(item, "continue_accepted")
        return response

    @app.post("/api/v1/executions/{eid}/finalize", status_code=202)
    async def finalize(eid: str, body: ExecutorFinalizeRequestBody):
        data = body.model_dump(mode="json")
        digest, prior = once((eid, "finalize", data["idempotency_key"]), data)
        if prior:
            return prior
        item = find(eid)
        if (
            item["status"] != "WAITING_FOR_OPERATION"
            or item["version"] != data["expected_version"]
        ):
            raise HTTPException(409, "Version/state mismatch")
        item["version"] += 1
        item["status"] = "SUCCEEDED"
        response = {
            "execution_id": eid,
            "operation": None,
            "state": {"status": "FINALIZING", "version": item["version"]},
        }
        receipts[(eid, "finalize", data["idempotency_key"])] = (
            digest,
            response,
        )
        record(item, "finalize_accepted")
        await event(
            item,
            "execution.completed",
            {
                "status": "SUCCEEDED",
                "operation_summary": {
                    "total": len(item["operations"]),
                    "succeeded": len(item["operations"]),
                    "failed": 0,
                    "cancelled": 0,
                },
                "retry": None,
                "error": None,
            },
        )
        return response

    @app.get("/api/v1/executions/{eid}/events")
    async def history(eid: str, after_sequence: int = 0, limit: int = 100):
        events = find(eid)["events"]
        page = [e for e in events if e["event_sequence"] > after_sequence][
            :limit
        ]
        more = bool(page and page[-1]["event_sequence"] < len(events))
        if measure["active"]:
            history_requests.append(
                {
                    "execution_id": eid,
                    "after_sequence": after_sequence,
                    "limit": limit,
                    "items": len(page),
                    "has_more": more,
                }
            )
        return {
            "items": page,
            "next_cursor": f"fixture:{page[-1]['event_sequence']}"
            if more
            else None,
            "has_more": more,
        }

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    import uvicorn

    uvicorn.run(
        create_app(cfg), host="127.0.0.1", port=cfg["port"], log_level="error"
    )

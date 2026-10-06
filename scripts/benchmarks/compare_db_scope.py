"""Reproducible A/B of immutable Git exports on an explicitly disposable DB.

Run with the project's Python 3.11. DTEST_BENCH_DATABASE_URL must point to a
local database named identity_test: this runner truncates its tables per trial.
No live deployment is touched. Raw request/server/database evidence is saved.
"""

import argparse, asyncio, json, math, os, random, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
from contextlib import AsyncExitStack
from uuid import uuid4
import httpx
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
BEFORE = "64ad96f"
AFTER = "4bb5c2f"
p = argparse.ArgumentParser()
p.add_argument("--output", required=True, type=Path)
p.add_argument(
    "--matrix",
    choices=["pilot", "calibrate", "main", "sensitivity"],
    default="main",
)
p.add_argument("--repeats", type=int, default=2)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
u = make_url(os.environ["DTEST_BENCH_DATABASE_URL"])
if u.database != "identity_test" or u.host not in ("127.0.0.1", "localhost"):
    raise SystemExit(
        "Only an explicitly disposable local identity_test database is allowed"
    )
DSN = u.set(drivername="postgresql").render_as_string(hide_password=False)
import platform

(a.output / "host.json").write_text(
    json.dumps(
        {
            "python": sys.version,
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "harness_source": str(ROOT / "scripts/benchmarks"),
        },
        indent=2,
    )
)
SOURCES = a.output / "sources"
SOURCES.mkdir(exist_ok=True)
for label, ref in [("before", BEFORE), ("after", AFTER)]:
    folder = SOURCES / label
    if not folder.exists():
        folder.mkdir()
        data = subprocess.check_output(["git", "archive", ref], cwd=ROOT)
        archive = a.output / f"{label}.tar"
        archive.write_bytes(data)
        subprocess.run(
            [
                "tar",
                "-xf",
                str(archive.resolve()),
                "-C",
                str(folder.resolve()),
            ],
            check=True,
        )
        archive.unlink()
# Migrations use only the explicitly selected scratch DB; no project .env.
config = a.output / "migration.yml"
config.write_text(
    "database_url: "
    + os.environ["DTEST_BENCH_DATABASE_URL"]
    + "\nCHECKPOINT_DB_URI: "
    + DSN
    + "\n"
)
env = {
    **os.environ,
    "PYTHONPATH": str(SOURCES.resolve() / "after" / "src"),
    "SERVICE_CONFIG_FILE": str(config.resolve()),
    "APP_ENV": "dev",
    "PYTHONDONTWRITEBYTECODE": "1",
}
with (a.output / "migration.log").open("w") as log:
    subprocess.run(
        [PY, "-m", "alembic", "-c", "alembic.crud.ini", "upgrade", "head"],
        cwd=SOURCES / "after",
        env=env,
        stdout=log,
        stderr=log,
        check=True,
    )
config.unlink()


def reset_db():
    with psycopg.connect(DSN, autocommit=True) as db:
        names = [
            row[0]
            for row in db.execute(
                "SELECT tablename FROM pg_tables WHERE "
                "schemaname='public' AND tablename <> "
                "'alembic_version'"
            )
        ]
        if names:
            db.execute(
                sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(",").join(map(sql.Identifier, names))
                )
            )


def db_evidence():
    with psycopg.connect(DSN) as db:
        runs = [
            dict(
                zip(
                    [
                        "run_id",
                        "session_id",
                        "status",
                        "created_at",
                        "started_at",
                        "completed_at",
                        "updated_at",
                        "attempt_count",
                        "failure",
                    ],
                    r,
                )
            )
            for r in db.execute(
                "SELECT run_id,session_id,status,created_at,started_"
                "at,completed_at,updated_at,attempt_count,failure "
                "FROM agent_runs ORDER BY "
                "created_at"
            )
        ]
        for r in runs:
            r["queue_ms"] = (
                (r["started_at"] - r["created_at"]).total_seconds() * 1000
                if r["started_at"]
                else None
            )
            finish = r["completed_at"] or (
                r["updated_at"] if r["status"] == "interrupted" else None
            )
            r["execution_ms"] = (
                (finish - r["started_at"]).total_seconds() * 1000
                if finish and r["started_at"]
                else None
            )
        return {
            "runs": runs,
            "run_statuses": dict(
                db.execute(
                    "SELECT status,count(*) FROM agent_runs GROUP BY status"
                ).fetchall()
            ),
            "recovery_tasks": db.execute(
                "SELECT count(*) FROM tasks WHERE recovery_required"
            ).fetchone()[0],
            "session_owners": db.execute(
                "SELECT count(*) FROM session_executions WHERE "
                "token IS NOT "
                "NULL"
            ).fetchone()[0],
            "messages": db.execute("SELECT count(*) FROM messages").fetchone()[
                0
            ],
            "logs": db.execute(
                "SELECT count(*) FROM agent_run_logs"
            ).fetchone()[0],
        }


async def trial(case, label, repeat):
    reset_db()
    key = f"{case['name']}-{label}-r{repeat}"
    folder = a.output / key
    folder.mkdir(exist_ok=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = {
        **case,
        "port": port,
        "label": label,
        "repeat": repeat,
        "commit": BEFORE if label == "before" else AFTER,
    }
    (folder / "config.json").write_text(json.dumps(cfg, indent=2))
    log = (folder / "server.log").open("w")
    child = subprocess.Popen(
        [
            PY,
            str(ROOT / "scripts/benchmarks/db_scope_server.py"),
            "--source",
            str((SOURCES / label).resolve()),
            "--config",
            str((folder / "config.json").resolve()),
        ],
        cwd=SOURCES / label,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=log,
        stderr=log,
    )
    requests = []
    scenarios = []
    stages = []
    background_tasks = []
    started = None
    limits = httpx.Limits(max_connections=400, max_keepalive_connections=200)
    try:
        async with (
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=10, limits=limits
            ) as client,
            AsyncExitStack() as clients,
        ):
            for _ in range(150):
                if child.poll() is not None:
                    raise RuntimeError(
                        f"{key}: server exited; see {folder}/server.log"
                    )
                try:
                    if (await client.get("/_bench/ready")).json()["ready"]:
                        break
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("startup deadline")
            users = []

            async def seed(i):
                r = await client.post(
                    "/api/v1/users",
                    headers={"X-User-Id": "admin"},
                    json={
                        "user_id": f"user-{i}",
                        "user_name": f"User {i}",
                        "role": "user",
                    },
                )
                r.raise_for_status()
                return r.json()

            for start in range(0, case["users"], 10):
                users.extend(
                    await asyncio.gather(
                        *(
                            seed(i)
                            for i in range(
                                start, min(start + 10, case["users"])
                            )
                        )
                    )
                )
            user_clients = {}
            for user in users:
                user_clients[
                    user["user_id"]
                ] = await clients.enter_async_context(
                    httpx.AsyncClient(
                        base_url=f"http://127.0.0.1:{port}",
                        timeout=10,
                        limits=httpx.Limits(
                            max_connections=2, max_keepalive_connections=2
                        ),
                    )
                )
            background_client = await clients.enter_async_context(
                httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    timeout=10,
                    limits=limits,
                )
            )
            # Separate per-user HTTP pools avoid load-generator pool contention.
            # Warm client/server routes without creating a graph run.
            for user in users:
                (
                    await user_clients[user["user_id"]].get(
                        f"/api/v1/projects/{user['default_project_id']}",
                        headers={"X-User-Id": user["user_id"]},
                    )
                ).raise_for_status()
            await client.post("/_bench/reset")
            started = time.perf_counter()

            async def request(method, path, user, kind, **kwargs):
                t = time.perf_counter()
                code = None
                err = None
                server_ms = None
                try:
                    hdr = {"X-User-Id": user["user_id"]}
                    if kind == "run_post":
                        hdr["Idempotency-Key"] = str(uuid4())
                    transport = (
                        background_client
                        if kind == "background_crud"
                        else user_clients[user["user_id"]]
                    )
                    response = await transport.request(
                        method, path, headers=hdr, **kwargs
                    )
                    server_ms = (
                        float(response.headers["X-Bench-Server-Ms"])
                        if "X-Bench-Server-Ms" in response.headers
                        else None
                    )
                    code = response.status_code
                    response.raise_for_status()
                    return response.json() if response.content else {}
                except Exception as exc:
                    err = type(exc).__name__
                    raise
                finally:
                    requests.append(
                        {
                            "kind": kind,
                            "method": method,
                            "start_s": t - started,
                            "ms": (time.perf_counter() - t) * 1000,
                            "status": code,
                            "error": err,
                            "server_ms": server_ms,
                        }
                    )

            async def user_job(user, index):
                t = time.perf_counter()
                status = "complete"
                error = None
                try:
                    if case["scenario"] == "crud":
                        for i in range(3):
                            pr = await request(
                                "POST",
                                "/api/v1/projects",
                                user,
                                "crud",
                                json={"project_name": f"project-{i}"},
                            )
                            se = await request(
                                "POST",
                                f"/api/v1/projects/{pr['id']}/sessions",
                                user,
                                "crud",
                                json={"session_name": "test"},
                            )
                            await request(
                                "GET",
                                f"/api/v1/sessions/{se['id']}",
                                user,
                                "crud",
                            )
                            await request(
                                "PATCH",
                                f"/api/v1/sessions/{se['id']}",
                                user,
                                "crud",
                                json={"session_name": "updated"},
                            )
                            await request(
                                "DELETE",
                                f"/api/v1/sessions/{se['id']}",
                                user,
                                "crud",
                            )
                            await request(
                                "DELETE",
                                f"/api/v1/projects/{pr['id']}",
                                user,
                                "crud",
                            )
                            await asyncio.sleep(0.1)
                    else:
                        se = await request(
                            "POST",
                            f"/api/v1/projects/{user['default_project_id']}/sessions",
                            user,
                            "session_create",
                            json={"session_name": "analysis"},
                        )
                        cmds = (
                            [None]
                            if case["scenario"] == "initial"
                            else [
                                None,
                                "mock",
                                {"objective": "EDA service comparison"},
                                {"candidate_number": 1},
                            ]
                        )
                        expected = [
                            "data_selection",
                            "analysis_context",
                            "workflow_candidate_selection",
                            "workflow_approval",
                        ]
                        previous = None
                        for step, command in enumerate(cmds):
                            payload = (
                                {
                                    "input": {
                                        "messages": [
                                            {
                                                "role": "user",
                                                "content": (
                                                    "불량 예측 서비스 비교"
                                                ),
                                            }
                                        ]
                                    }
                                }
                                if command is None
                                else {
                                    "command": command,
                                    "metadata": {"resume_run_id": previous},
                                }
                            )
                            begin = time.perf_counter()
                            run = await request(
                                "POST",
                                f"/api/v1/sessions/{se['id']}/runs",
                                user,
                                "run_post",
                                json=payload,
                            )
                            runid = run.get("run_id", run.get("id"))
                            polls = 0
                            while run["status"] in ("pending", "running"):
                                await asyncio.sleep(0.5)
                                polls += 1
                                run = await request(
                                    "GET",
                                    f"/api/v1/sessions/{se['id']}/runs/{runid}",
                                    user,
                                    "run_get",
                                )
                            actions = [
                                x.get("name")
                                for it in run.get("interrupt") or []
                                for x in it.get("action_requests", [])
                            ]
                            stages.append(
                                {
                                    "user": index,
                                    "run_id": runid,
                                    "stage": expected[step],
                                    "ms": (time.perf_counter() - begin) * 1000,
                                    "polls": polls,
                                    "status": run["status"],
                                }
                            )
                            if run["status"] != "interrupted" or actions != [
                                expected[step]
                            ]:
                                raise RuntimeError("unexpected run result")
                            previous = runid
                            if step < len(cmds) - 1:
                                await asyncio.sleep(0.2)
                except asyncio.TimeoutError:
                    status = "deadline"
                    error = "scenario deadline"
                except Exception as exc:
                    status = "error"
                    error = type(exc).__name__
                finally:
                    scenarios.append(
                        {
                            "user": index,
                            "ms": (time.perf_counter() - t) * 1000,
                            "status": status,
                            "error": error,
                        }
                    )

            async def bounded_job(user, i):
                try:
                    async with asyncio.timeout(case["deadline"]):
                        await user_job(user, i)
                except TimeoutError:
                    if not any(x["user"] == i for x in scenarios):
                        scenarios.append(
                            {
                                "user": i,
                                "status": "deadline",
                                "ms": case["deadline"] * 1000,
                            }
                        )
                    else:
                        row = next(x for x in scenarios if x["user"] == i)
                        row["status"] = "deadline"
                        row["error"] = "scenario deadline"

            stop = asyncio.Event()

            async def background():
                # Fixed offered rate; no next-request-on-response feedback loop.
                index = 0
                base = time.perf_counter()

                async def op(j):
                    user = users[j % len(users)]
                    try:
                        if j % 2:
                            await request(
                                "GET",
                                f"/api/v1/projects/{user['default_project_id']}",
                                user,
                                "background_crud",
                            )
                        else:
                            await request(
                                "POST",
                                f"/api/v1/projects/{user['default_project_id']}/sessions",
                                user,
                                "background_crud",
                                json={"session_name": "background"},
                            )
                    except Exception:
                        pass

                while not stop.is_set():
                    background_tasks.append(asyncio.create_task(op(index)))
                    index += 1
                    await asyncio.sleep(
                        max(0, base + index / 20 - time.perf_counter())
                    )

            bg = (
                asyncio.create_task(background())
                if case["scenario"] != "crud"
                else None
            )
            await asyncio.gather(
                *(bounded_job(u, i) for i, u in enumerate(users))
            )
            workload_end = time.perf_counter()
            stop.set()
            if bg:
                await bg
            await asyncio.gather(*background_tasks)
            server = (await client.get("/_bench/metrics")).json()
            evidence = await asyncio.to_thread(db_evidence)
            result = {
                "config": cfg,
                "elapsed_s": workload_end - started,
                "tail_s": time.perf_counter() - workload_end,
                "requests": requests,
                "scenarios": scenarios,
                "stages": stages,
                "server": server,
                "database": evidence,
            }
            (folder / "raw.json").write_text(
                json.dumps(result, default=str, separators=(",", ":"))
            )
            complete = sum(x["status"] == "complete" for x in scenarios)
            print(
                json.dumps(
                    {
                        "trial": key,
                        "seconds": round(result["elapsed_s"], 2),
                        "complete": complete,
                        "users": case["users"],
                        "http_errors": sum(bool(r["error"]) for r in requests),
                        "healthy": server["healthy"],
                    }
                ),
                flush=True,
            )
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
            try:
                await asyncio.to_thread(child.wait, 12)
            except subprocess.TimeoutExpired:
                child.kill()
                await asyncio.to_thread(child.wait)
        log.close()


def case(name, scenario, users, delay=1000, pool=5, slots=4, deadline=80):
    return {
        "name": name,
        "scenario": scenario,
        "users": users,
        "delay_ms": delay,
        "pool": pool,
        "slots": slots,
        "deadline": deadline,
    }


if a.matrix == "pilot":
    cases = [
        case("pilot-initial10", "initial", 10),
        case("pilot-flow10", "flow", 10, 100),
        case("pilot-crud10", "crud", 10),
    ]
elif a.matrix == "calibrate":
    cases = [
        case("calibrate-crud100", "crud", 100),
        case("calibrate-mixed30", "initial", 30),
    ]
elif a.matrix == "main":
    cases = [case(f"crud-{n}", "crud", n) for n in (1, 10, 30, 50, 100)]
    cases += [case(f"mixed-{n}", "initial", n) for n in (1, 10, 30, 50, 100)]
    cases += [case(f"flow-{n}", "flow", n, 100) for n in (1, 10, 30, 50, 100)]
else:
    cases = [
        case("fast-mixed10", "initial", 10, 100),
        case("slow-mixed10", "initial", 10, 5000),
        case("tight-slow10", "initial", 10, 5000, pool=4, deadline=45),
        case("roomy-mixed100", "initial", 100, pool=10),
        case("single-slot10", "initial", 10, slots=1),
    ]
(a.output / f"{a.matrix}-plan.json").write_text(
    json.dumps(
        {
            "before": BEFORE,
            "after": AFTER,
            "repeats": a.repeats,
            "cases": cases,
            "poll_seconds": 0.5,
            "hitl_think_seconds": 0.2,
            "background_crud_rps": 20,
            "client_pools": (
                "one 2-connection pool per virtual user; separate "
                "background/control "
                "pools"
            ),
        },
        indent=2,
    )
)


async def main():
    for c in cases:
        for repeat in range(1, a.repeats + 1):
            for label in (
                ["before", "after"] if repeat % 2 else ["after", "before"]
            ):
                await trial(c, label, repeat)


asyncio.run(main())

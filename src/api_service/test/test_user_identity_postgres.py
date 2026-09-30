"""Opt-in real PostgreSQL tests. Use a DISPOSABLE identity_test database only.

DTEST_IDENTITY_TEST_DATABASE_URL=postgresql+asyncpg://.../identity_test
This suite migrates and truncates that dedicated database, never application DBs.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import service_settings
from api_service.core.auth import Actor, get_current_user_id
from api_service.core.database import get_db
from api_service.core.enums import AgentRunStatus, DeleteYN, MessageType, TaskStatus, UserRole
from api_service.models.common.user_model import UserModel
from api_service.models.common.project_model import ProjectModel, ProjectMemberModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.message_model import MessageModel
from api_service.models.common.task_model import TaskModel
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.schemas.common.user_schema import UserCreate
from api_service.services.user_service import UserService
from service_bootstrap import create_app

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def database_url(tmp_path_factory):
    value = os.getenv("DTEST_IDENTITY_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Needs dedicated local identity_test PostgreSQL")
    url = make_url(value)
    assert url.database in {"identity_test", "agentic_regression_test"} and url.host in {"127.0.0.1", "localhost"}
    raw_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    config_path = tmp_path_factory.mktemp("identity-migrations") / "config.yml"
    config_path.write_text("service:\n  database_url: " + value + "\n  checkpoint_db_uri: " + raw_url + "\n")
    environment = {**os.environ, "SERVICE_CONFIG_FILE": str(config_path), "APP_ENV": "dev", "PYTHONPATH": str(ROOT / "src")}
    with psycopg.connect(raw_url, autocommit=True) as db:
        db.execute("DROP SCHEMA public CASCADE")
        db.execute("CREATE SCHEMA public")

    def migrate(direction, revision):
        result = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.crud.ini", direction, revision],
                                cwd=ROOT, env=environment, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "20260907_0017")
    user_id, project_id = uuid4(), uuid4()
    with psycopg.connect(raw_url) as db:
        # Keep the fixture reproducible on the dedicated scratch DB.
        db.execute("TRUNCATE users CASCADE")
        db.execute("INSERT INTO users(user_id,user_name) VALUES(%s,'legacy')", (user_id,))
        db.execute("INSERT INTO projects(project_id,user_id,project_name,is_default) VALUES(%s,%s,'default',true)",
                   (project_id, user_id))
    migrate("upgrade", "head")
    with psycopg.connect(raw_url) as db:
        assert db.execute("SELECT public_user_id,role FROM users WHERE user_id=%s", (user_id,)).fetchone() == (str(user_id), "user")
        assert db.execute("SELECT user_id FROM projects WHERE project_id=%s", (project_id,)).fetchone()[0] == user_id
    migrate("downgrade", "20260907_0017")
    with psycopg.connect(raw_url) as db:
        assert db.execute("SELECT user_id FROM projects WHERE project_id=%s", (project_id,)).fetchone()[0] == user_id
    migrate("upgrade", "head")
    return value


@pytest_asyncio.fixture
async def harness(database_url, monkeypatch):
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    monkeypatch.setattr(service_settings, "_snapshot", None)
    settings = service_settings.load_settings(config={"DATABASE_URL": database_url,
        "CHECKPOINT_DB_URI": make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False),
        "AGENT_WORKER_ENABLED": False, "TASK_RECONCILER_ENABLED": False, "EVENT_WORKER_ENABLED": False,
        "MODEL_PROVIDER": "mock", "EXECUTOR_SUBMIT_ENABLED": False}, environ={})
    app = create_app(settings)

    async def request_db():
        async with factory() as db:
            try:
                yield db
            except BaseException:
                await db.rollback()
                raise
    app.dependency_overrides[get_db] = request_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, factory=factory, app=app, engine=engine)
    await engine.dispose()


def headers(user_id="admin"):
    return {"X-User-Id": user_id}


async def initialize(h):
    async with h.factory() as db:
        return await UserService.bootstrap_admin(db, UserCreate(user_id="admin", user_name="Admin", role="admin"))


async def add_user(h, name="user-a", role="user"):
    response = await h.client.post("/api/v1/users", headers=headers(),
                                   json={"user_id": name, "user_name": "Display Name", "role": role})
    assert response.status_code == 201, response.text
    return response.json()


async def add_session(h, user):
    response = await h.client.post(f"/api/v1/projects/{user['default_project_id']}/sessions", headers=headers(user["user_id"]),
                                   json={"session_name": "Test"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_bootstrap_is_once_and_does_not_change_existing_identity(harness):
    h = harness
    a = await initialize(h)
    b = await initialize(h)
    assert a.user_id == b.user_id and a.default_project_id == b.default_project_id
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(UserModel)) == 1
        assert await db.scalar(select(func.count()).select_from(ProjectModel)) == 1
        assert await db.scalar(select(func.count()).select_from(ProjectMemberModel)) == 1
        with pytest.raises(HTTPException) as exc:
            await UserService.bootstrap_admin(db, UserCreate(user_id="other", user_name="Other", role="admin"))
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_header_identity_roles_and_user_management(harness):
    h = harness
    await initialize(h)
    for supplied in ({}, {"Authorization": "Bearer admin"}, headers("missing"), headers("bad/id")):
        assert (await h.client.get("/api/v1/users/me", headers=supplied)).status_code == 401
    user = await add_user(h)
    assert user["role"] == "user" and user["default_project_id"]
    me = await h.client.get("/api/v1/users/me", headers=headers("USER-A"))
    assert me.status_code == 200 and me.json()["user_id"] == "user-a"
    assert "token" not in me.json()
    for method, path, body in [("POST", "/users", {"user_id": "new", "user_name": "new", "role": "admin"}),
                              ("PATCH", "/users/user-a", {"role": "admin"}),
                              ("DELETE", "/users/user-a", None)]:
        response = await h.client.request(method, "/api/v1"+path, headers=headers("user-a"), json=body)
        assert response.status_code == 403, response.text
    assert (await h.client.get("/api/v1/users/admin", headers=headers("user-a"))).status_code == 404
    assert (await h.client.get("/api/v1/users/user-a", headers=headers())).status_code == 200
    assert (await h.client.patch("/api/v1/users/user-a", headers=headers(), json={"role": "admin"})).json()["role"] == "admin"
    assert (await h.client.post("/api/v1/users", headers=headers("user-a"),
                               json={"user_id": "new-user", "user_name": "Duplicate names allowed"})).status_code == 201


@pytest.mark.asyncio
async def test_ownership_stays_isolated_even_for_admin(harness):
    h = harness
    await initialize(h)
    user = await add_user(h)
    session_id = await add_session(h, user)
    for actor in ("admin",):
        assert (await h.client.get("/api/v1/projects/"+user["default_project_id"], headers=headers(actor))).status_code == 404
        assert (await h.client.get("/api/v1/sessions/"+session_id, headers=headers(actor))).status_code == 404
        assert (await h.client.get(f"/api/v1/sessions/{session_id}/runs", headers=headers(actor))).status_code == 404
    projects = await h.client.get("/api/v1/projects", headers=headers("user-a"))
    assert [p["id"] for p in projects.json()["items"]] == [user["default_project_id"]]


@pytest.mark.asyncio
async def test_duplicate_registration_race_is_atomic(harness):
    h = harness
    await initialize(h)
    async def register():
        return await h.client.post("/api/v1/users", headers=headers(),
                                   json={"user_id": "racer", "user_name": "Racer"})
    result = await asyncio.wait_for(asyncio.gather(register(), register()), 5)
    assert sorted(r.status_code for r in result) == [201, 409]
    async with h.factory() as db:
        user = await db.scalar(select(UserModel).where(UserModel.public_user_id == "racer"))
        assert await db.scalar(select(func.count()).select_from(ProjectModel).where(ProjectModel.user_id == user.user_id)) == 1


@pytest.mark.asyncio
async def test_failed_default_project_creation_rolls_back_user(harness):
    h = harness
    await initialize(h)
    async with h.engine.begin() as conn:
        await conn.execute(text("ALTER TABLE projects ADD CONSTRAINT test_reject_default CHECK (NOT is_default) NOT VALID"))
    try:
        with pytest.raises(Exception):
            await add_user(h, "atomic")
        async with h.factory() as db:
            assert await db.scalar(select(UserModel).where(UserModel.public_user_id == "atomic")) is None
    finally:
        async with h.engine.begin() as conn:
            await conn.execute(text("ALTER TABLE projects DROP CONSTRAINT test_reject_default"))


@pytest.mark.asyncio
async def test_last_admin_concurrent_demotions_keep_one(harness):
    h = harness
    await initialize(h)
    await add_user(h, "admin-two", "admin")
    async def demote(identity):
        return await h.client.patch("/api/v1/users/"+identity, headers=headers(identity), json={"role": "user"})
    responses = await asyncio.wait_for(asyncio.gather(demote("admin"), demote("admin-two")), 5)
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(UserModel).where(
            UserModel.role == UserRole.ADMIN, UserModel.delete_yn == DeleteYN.N)) == 1


@pytest.mark.asyncio
async def test_last_admin_delete_is_rejected(harness):
    h = harness
    await initialize(h)
    assert (await h.client.delete("/api/v1/users/admin", headers=headers())).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING_INPUT])
async def test_unfinished_task_blocks_user_deletion(harness, status):
    h = harness
    await initialize(h)
    user = await add_user(h)
    session_id = await add_session(h, user)
    async with h.factory() as db:
        db.add(TaskModel(session_id=UUID(session_id), status=status, idempotency_key="active"))
        await db.commit()
    result = await h.client.delete("/api/v1/users/user-a", headers=headers())
    assert result.status_code == 409, result.text
    assert (await h.client.get("/api/v1/users/me", headers=headers("user-a"))).status_code == 200


@pytest.mark.asyncio
async def test_finished_history_does_not_block_cascade_and_id_is_reserved(harness):
    h = harness
    await initialize(h)
    user = await add_user(h)
    session_id = await add_session(h, user)
    async with h.factory() as db:
        task = TaskModel(session_id=UUID(session_id), status=TaskStatus.SUCCESS, idempotency_key="finished")
        db.add(task)
        await db.flush()
        db.add(AgentRunModel(session_id=UUID(session_id), task_id=task.task_id, status=AgentRunStatus.INTERRUPTED,
                            idempotency_key="old-segment"))
        db.add(MessageModel(session_id=UUID(session_id), message_type=MessageType.USER, content_text="old"))
        await db.commit()
    response = await h.client.delete("/api/v1/users/user-a", headers=headers())
    assert response.status_code == 204, response.text
    assert (await h.client.get("/api/v1/users/me", headers=headers("user-a"))).status_code == 401
    assert (await h.client.get("/api/v1/projects", headers=headers("user-a"))).status_code == 401
    assert (await h.client.post("/api/v1/users", headers=headers(),
                               json={"user_id": "user-a", "user_name": "reuse"})).status_code == 409
    async with h.factory() as db:
        for model in (UserModel, ProjectModel, SessionModel, MessageModel):
            query = select(func.count()).select_from(model).where(model.delete_yn == DeleteYN.Y)
            assert await db.scalar(query) == 1


@pytest.mark.asyncio
async def test_deletion_waits_for_admitted_run_then_rejects(harness):
    h = harness
    await initialize(h)
    user = await add_user(h)
    session_id = await add_session(h, user)
    async with h.factory() as admitted:
        row = await admitted.scalar(select(UserModel).where(UserModel.public_user_id == "user-a"))
        actor = Actor(row.user_id, row.public_user_id, row.role)
        await get_current_user_id(actor.public_user_id, admitted)  # Real FOR SHARE admission.
        deleting = asyncio.create_task(h.client.delete("/api/v1/users/user-a", headers=headers()))
        try:
            await asyncio.sleep(.1)
            assert not deleting.done()
            admitted.add(TaskModel(session_id=UUID(session_id), status=TaskStatus.PENDING, idempotency_key="racing-run"))
            await admitted.commit()
            result = await asyncio.wait_for(deleting, 5)
            assert result.status_code == 409, result.text
        finally:
            await admitted.rollback()
            if not deleting.done():
                deleting.cancel()
                await asyncio.gather(deleting, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_actor_cannot_register_after_demotion(harness):
    h = harness
    await initialize(h)
    await add_user(h, "admin-two", "admin")
    async with h.factory() as db:
        row = await db.scalar(select(UserModel).where(UserModel.public_user_id == "admin-two"))
        stale = Actor(row.user_id, row.public_user_id, row.role)
    assert (await h.client.patch("/api/v1/users/admin-two", headers=headers(), json={"role": "user"})).status_code == 200
    async with h.factory() as db:
        with pytest.raises(HTTPException) as exc:
            await UserService.create(db, stale, UserCreate(user_id="forbidden", user_name="Forbidden"))
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_bootstrap_concurrent_calls_create_one_admin(harness):
    h = harness
    first, second = await asyncio.wait_for(asyncio.gather(initialize(h), initialize(h)), 5)
    assert first.default_project_id == second.default_project_id
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(UserModel)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [AgentRunStatus.PENDING, AgentRunStatus.RUNNING, AgentRunStatus.INTERRUPTED])
async def test_orphan_nonterminal_run_blocks_deletion(harness, status):
    h = harness
    await initialize(h)
    user = await add_user(h)
    session_id = await add_session(h, user)
    async with h.factory() as db:
        db.add(AgentRunModel(session_id=UUID(session_id), status=status, idempotency_key="orphan"))
        await db.commit()
    assert (await h.client.delete("/api/v1/users/user-a", headers=headers())).status_code == 409


@pytest.mark.asyncio
async def test_header_maps_run_admission_to_internal_uuid_and_sse(harness, monkeypatch):
    h = harness
    await initialize(h)
    user = await add_user(h)
    session_id = await add_session(h, user)
    result = await h.client.post(f"/api/v1/sessions/{session_id}/runs",
        headers={**headers("user-a"), "Idempotency-Key": "new-run"},
        json={"input": {'content': [{'type': 'text', 'text': "hello"}]}})
    assert result.status_code == 202, result.text
    run_id = result.json()["id"]
    async with h.factory() as db:
        stored_user = await db.scalar(select(UserModel).where(UserModel.public_user_id == "user-a"))
        run = await db.get(AgentRunModel, UUID(run_id))
        assert run.metadata_json["requested_by_user_id"] == str(stored_user.user_id)
        # Finish without executing graph/LLM/Executor, then exercise the stream.
        run.status = AgentRunStatus.SUCCESS
        await db.execute(update(TaskModel).where(TaskModel.task_id == run.task_id).values(status=TaskStatus.SUCCESS))
        await db.commit()
    import api_service.api.v1.routes.runs as routes
    monkeypatch.setattr(routes, "get_session_factory", lambda: h.factory)
    stream = await asyncio.wait_for(h.client.get(f"/api/v1/sessions/{session_id}/runs/{run_id}/stream",
                                                headers=headers("user-a")), 5)
    assert stream.status_code == 200 and "event: run.updated" in stream.text
    assert (await h.client.get(f"/api/v1/sessions/{session_id}/runs/{run_id}/stream",
                               headers=headers())).status_code == 404


@pytest.mark.asyncio
async def test_new_admission_waits_for_deletion_then_fails(harness):
    h = harness
    await initialize(h)
    await add_user(h)
    async with h.factory() as deleting:
        target = await deleting.scalar(select(UserModel).where(UserModel.public_user_id == "user-a").with_for_update())
        actor = Actor(target.user_id, target.public_user_id, target.role)

        async def new_request():
            async with h.factory() as db:
                return await get_current_user_id(actor.public_user_id, db)

        entering = asyncio.create_task(new_request())
        try:
            await asyncio.sleep(.1)
            assert not entering.done()
            target.delete_yn = DeleteYN.Y
            await deleting.commit()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(entering, 5)
            assert exc.value.status_code == 401
        finally:
            await deleting.rollback()
            if not entering.done():
                entering.cancel()
                await asyncio.gather(entering, return_exceptions=True)


@pytest.mark.asyncio
async def test_bootstrap_command_uses_selected_config_and_is_idempotent(harness, database_url, tmp_path):
    h = harness
    config = tmp_path / "bootstrap.yml"
    config.write_text("service:\n  database_url: " + database_url + "\n")
    command = [sys.executable, "-m", "bootstrap_admin", "--config", str(config),
               "--user-id", "first-admin", "--user-name", "First Admin"]
    for _ in range(2):
        result = await asyncio.to_thread(subprocess.run, command, cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "APP_ENV": "dev"},
            text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert '"user_id":"first-admin"' in result.stdout
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(UserModel)) == 1
        assert await db.scalar(select(func.count()).select_from(ProjectModel)) == 1

"""Offline evidence for the 2026-09-28 CRUD review, not regression acceptance tests.

Run: .venv/bin/python scripts/diagnostics/review_crud_contracts.py
No application lifespan, real database, Redis, LLM, or Executor is started.
Repositories are fakes; this does not verify PostgreSQL/concurrent transactions.
"""

import ast
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/reports/crud-api-review-2026-09-28"
sys.path.insert(0, str(ROOT / "src"))


def inventory():
    """Read the current business router modules; saved 2026-09-28 evidence stays historical."""
    result = []
    for name in ("users", "projects", "sessions", "messages", "runs",
                 "workflows", "run_diagnostics"):
        path = ROOT / f"src/api_service/api/v1/routes/{name}.py"
        tree = ast.parse(path.read_text())
        prefix = ""
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
                    prefix = next((ast.literal_eval(k.value) for k in node.value.keywords
                                   if k.arg == "prefix"), "")
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and isinstance(deco.func.value, ast.Name)
                        and deco.func.value.id == "router"
                        and deco.func.attr in {"get", "post", "patch", "delete", "put"}):
                    result.append(dict(group=name, method=deco.func.attr.upper(),
                                       path="/api/v1" + prefix + ast.literal_eval(deco.args[0]),
                                       handler=node.name, source=str(path.relative_to(ROOT)),
                                       line=node.lineno))
    return result


async def probes():
    from fastapi import FastAPI, Response
    from fastapi.security import HTTPAuthorizationCredentials
    from fastapi.testclient import TestClient
    from api_service.api.v1.routes import messages, users
    from api_service.api.dependencies import get_current_user_id
    from api_service.infrastructure.database import get_db
    from api_service.models.enums import DeleteYN
    from api_service.repositories.session_repository import SessionRepository
    from api_service.repositories.user_repository import UserRepository
    from api_service.schemas.message_schema import MessageCreate
    from api_service.schemas.api_schema import SessionResource
    from api_service.schemas.project_schema import ProjectResource
    from api_service.resources.sessions import SessionService

    now, owner, project = datetime.now(timezone.utc), uuid4(), uuid4()
    results = []

    # A small app with the actual users router, fake database and fake service.
    # No root router lifespan is registered, hence no background workers.
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1")

    async def fake_db():
        yield object()

    app.dependency_overrides[get_db] = fake_db
    user = dict(user_id=owner, user_name="offline-review", delete_yn="N",
                created_at=now, updated_at=now, deleted_at=None)
    with patch.object(users.UserService, "read", AsyncMock(return_value=user)), \
         patch.object(users.UserService, "update", AsyncMock(return_value=user)), \
         patch.object(users.UserService, "delete", AsyncMock(return_value=None)), \
         TestClient(app) as client:
        statuses = {
            "GET": client.get(f"/api/v1/users/{owner}").status_code,
            "PATCH": client.patch(f"/api/v1/users/{owner}",
                                  json={"user_name": "offline-review"}).status_code,
            "DELETE": client.delete(f"/api/v1/users/{owner}").status_code,
        }
    assert statuses == {"GET": 200, "PATCH": 200, "DELETE": 204}
    results.append(dict(check="user_routes_without_authorization", observed=statuses,
                        scope="Actual router with fake service/database; no deployed gateway tested."))

    with patch.object(UserRepository, "get_active", AsyncMock(return_value=object())):
        authenticated = await get_current_user_id(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=str(owner)), object())
    assert authenticated == owner
    results.append(dict(check="active_user_uuid_as_bearer", observed="accepted",
                        scope="Actual auth function, active-user repository stubbed."))

    roles = [MessageCreate(content_text="offline", message_type=role).message_type.value
             for role in ("user", "system", "assistant", "agent", "tool")]
    results.append(dict(check="public_message_schema_roles", observed=roles))

    class FakeDB:
        def __init__(self):
            self.sessions = {}
            self.messages = []

        def add(self, message):
            self.messages.append(message)

        async def flush(self):
            for i, message in enumerate(self.messages):
                if message.message_id is None:
                    message.message_id = uuid4()
                    message.sequence_no = i + 1
                    message.created_at = message.updated_at = now

        async def commit(self):
            pass

        async def refresh(self, _):
            pass

        async def scalar(self, query):
            params = query.compile().params
            assert set(params) == {"session_id_1", "client_request_id_1", "delete_yn_1"}
            return next((m for m in self.messages
                         if m.session_id == params["session_id_1"]
                         and m.client_request_id == params["client_request_id_1"]
                         and m.delete_yn == DeleteYN.N), None)

    db = FakeDB()

    async def create_session(db, *, user_id, project_id, session_name):
        session = SimpleNamespace(session_id=uuid4(), user_id=user_id, project_id=project_id,
                                  session_name=session_name, current_leaf_message_id=None)
        db.sessions[session.session_id] = session
        return session

    async def owned_session(db, *, user_id, session_id, for_update):
        session = db.sessions.get(session_id)
        return session if session is not None and session.user_id == user_id else None

    with patch.object(SessionService, "create_internal", create_session), \
         patch.object(SessionRepository, "get_active_by_user", owned_session):
        receipts = []
        for _ in range(2):
            receipts.append(await messages.create_message_without_session(
                MessageCreate(project_id=project, content_text="same request"), Response(),
                "same-idempotency-key", owner, db))
        assert len(db.sessions) == 2 and len(db.messages) == 2
        assert receipts[0].session_id != receipts[1].session_id
        results.append(dict(check="sessionless_message_same_key_retry",
                            observed={"sessions_created": len(db.sessions),
                                      "messages_created": len(db.messages)},
                            scope="Actual route and MessageService; session creation/storage are fakes."))

        session_id = receipts[0].session_id
        before = len(db.messages)
        replay = await messages.create_message(
            session_id, MessageCreate(content_text="different request body"), Response(),
            "same-idempotency-key", owner, db)
        assert len(db.messages) == before and replay.message.content_text == "same request"
        results.append(dict(check="existing_session_same_key_different_body",
                            observed="old message returned without conflict or new insert"))

    project_response = ProjectResource.model_validate(SimpleNamespace(
        project_id=project, project_name="offline", system_prompt="", prompt_version=1,
        is_default=False, created_at=now, updated_at=now, sessions=["already loaded child"]
    )).model_dump()
    session_response = SessionResource.model_validate(SimpleNamespace(
        active_run=None, availability={"status":"available","allowed_actions":["send_message"],"reason":None},
        session_id=uuid4(), project_id=project, session_name="offline", settings={},
        current_leaf_message_id=None, created_at=now, updated_at=now,
        messages=["already loaded child"]
    )).model_dump()
    assert "sessions" not in project_response and "messages" not in session_response
    results.append(dict(check="detail_response_projection", observed={
        "project_omits_sessions": True, "session_omits_messages": True},
        scope="Actual response models; unbounded child queries separately confirmed by source review."))
    return results


def main():
    rows = inventory()
    # Settings uses a relative .env. Import only after leaving the repository so
    # this diagnostic does not load the real .env. Engine creation is forbidden.
    import sqlalchemy.ext.asyncio
    previous_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="crud-contract-review-") as isolated:
        try:
            os.chdir(isolated)
            with patch.object(sqlalchemy.ext.asyncio, "create_async_engine",
                              side_effect=AssertionError("Real DB engine forbidden in offline review")):
                results = asyncio.run(probes())
        finally:
            os.chdir(previous_cwd)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "inventory.json").write_text(json.dumps(rows, indent=2) + "\n")
    evidence = dict(mode="offline mocked-boundary probes; current behavior, not desired behavior",
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    endpoint_count=len(rows), groups=dict(Counter(r["group"] for r in rows)),
                    checks=results)
    (OUTPUT / "offline-checks.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()

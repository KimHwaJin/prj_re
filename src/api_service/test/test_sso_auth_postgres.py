"""Cookie-authenticated API with real isolated PostgreSQL; corporate SDK is an explicit double."""
import json
import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from api_service.core.enums import DeleteYN
from api_service.models.common.project_model import ProjectModel, ProjectMemberModel
from api_service.models.common.user_model import UserModel
from api_service.schemas.common.user_schema import UserCreate
from api_service.services.sso_user_service import SsoUserDirectory
from api_service.services.user_service import UserService
from api_service.test.test_user_identity_postgres import database_url, harness
from api_service.test.test_planning_api_postgres import test_config, planning, execute
from api_service.test.test_sso_auth import CorporateDouble, MemoryRedis
from service_auth.sso.dependencies import get_login_session
from service_auth.sso.sessions import RedisSessions
from service_auth.sso.settings import SsoSettings


def enable_cookie_boundary(h, employee_id="000123"):
    # Remove the historical business identity double; all requests below use production auth.
    h.app.dependency_overrides.pop(get_login_session, None)
    h.app.state.sso.settings=SsoSettings(public_api_origin="http://test", frontend_origin="http://test",
        allowed_origins=("https://sso.example.test",), cookie_secure=False,
        allowed_return_roots=("/", "/projects"))
    h.app.state.sso.sessions=RedisSessions(MemoryRedis(),"sso-postgres-test:dev")
    h.app.state.sso.users=SsoUserDirectory(auto_register=True,session_factory=h.factory)
    adapter=CorporateDouble()
    from service_auth.sso.contracts import VerifiedEmployee
    adapter.employee=VerifiedEmployee(employee_id,"SSO Employee")
    h.app.state.sso.adapter=adapter
    return adapter


@pytest_asyncio.fixture
async def cookie_api(harness):
    h=harness
    enable_cookie_boundary(h)
    yield h
    await h.app.state.sso.close()


async def sign_in(h):
    response=await h.client.get("/api/v1/auth/login/sso",follow_redirects=False)
    assert response.status_code==302,response.text
    me=await h.client.get("/api/v1/users/me")
    assert me.status_code==200,me.text
    return me.json()


@pytest.mark.asyncio
async def test_first_sso_login_atomically_creates_user_default_project_and_membership(cookie_api):
    h=cookie_api
    user=await sign_in(h)
    assert user["user_id"]=="000123" and user["role"]=="user" and user["default_project_id"]
    async with h.factory() as db:
        row=await db.scalar(select(UserModel).where(UserModel.public_user_id=="000123"))
        project=await db.scalar(select(ProjectModel).where(ProjectModel.project_id==UUID(user["default_project_id"])))
        membership=await db.scalar(select(ProjectMemberModel).where(ProjectMemberModel.project_id==project.project_id))
        assert project.user_id==row.user_id and membership.user_id==row.user_id
        assert membership.member_role.value=="owner"
    again=await sign_in(h)
    assert again["default_project_id"]==user["default_project_id"]


@pytest.mark.asyncio
async def test_parallel_first_logins_converge_on_one_user_and_default_project(cookie_api):
    h=cookie_api
    results=await asyncio.gather(*[h.client.get("/api/v1/auth/login/sso",follow_redirects=False) for _ in range(8)])
    assert all(r.status_code==302 for r in results),[r.text for r in results]
    async with h.factory() as db:
        for model in (UserModel,ProjectModel,ProjectMemberModel):
            assert await db.scalar(select(func.count()).select_from(model))==1


@pytest.mark.asyncio
async def test_existing_admin_internal_id_role_and_name_are_preserved(cookie_api):
    h=cookie_api
    async with h.factory() as db:
        original=await UserService.bootstrap_admin(db,UserCreate(user_id="000123",user_name="Existing Admin",role="admin"))
    async with h.factory() as db:
        internal_id=await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id=="000123"))
    me=await sign_in(h)
    assert me["role"]=="admin" and me["user_name"]=="Existing Admin"
    assert me["default_project_id"]==str(original.default_project_id)
    async with h.factory() as db:
        assert await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id=="000123"))==internal_id
    response=await h.client.post("/api/v1/users",headers={"X-CSRF-Token":me["csrf_token"]},
        json={"user_id":"other","user_name":"Other","role":"user"})
    assert response.status_code==201,response.text


@pytest.mark.asyncio
async def test_cookie_crud_ownership_csrf_and_role_enforcement(cookie_api):
    h=cookie_api
    me=await sign_in(h)
    assert (await h.client.post("/api/v1/projects",json={"project_name":"No token"})).status_code==403
    headers={"X-CSRF-Token":me["csrf_token"]}
    project=await h.client.post("/api/v1/projects",headers=headers,json={"project_name":"Cookie project"})
    assert project.status_code==201,project.text
    assert (await h.client.post("/api/v1/users",headers=headers,
        json={"user_id":"promote","user_name":"Promote","role":"admin"})).status_code==403
    from service_auth.sso.contracts import VerifiedEmployee
    h.app.state.sso.adapter.employee=VerifiedEmployee("000456","Other Employee")
    other=await sign_in(h)
    assert other["role"]=="user"
    assert (await h.client.get("/api/v1/projects/"+project.json()["id"])).status_code==404


@pytest.mark.asyncio
async def test_inactive_user_rejected_and_never_reactivated(cookie_api):
    h=cookie_api
    await sign_in(h)
    async with h.factory() as db:
        await db.execute(update(UserModel).where(UserModel.public_user_id=="000123").values(delete_yn=DeleteYN.Y))
        await db.commit()
    assert (await h.client.get("/api/v1/users/me")).status_code==401
    assert (await h.client.get("/api/v1/auth/login/sso",follow_redirects=False)).status_code==403
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(UserModel))==1
        assert await db.scalar(select(UserModel.delete_yn).where(UserModel.public_user_id=="000123"))==DeleteYN.Y


@pytest.mark.asyncio
async def test_sdk_employee_errors_and_disabled_auto_provision_do_not_create_rows(cookie_api):
    h=cookie_api
    h.app.state.sso.users=SsoUserDirectory(auto_register=False,session_factory=h.factory)
    assert (await h.client.get("/api/v1/auth/login/sso",follow_redirects=False)).status_code==403
    h.app.state.sso.users=SsoUserDirectory(auto_register=True,session_factory=h.factory)
    from service_auth.sso.contracts import VerifiedEmployee
    h.app.state.sso.adapter.employee=VerifiedEmployee("bad/id","Employee")
    assert (await h.client.get("/api/v1/auth/login/sso",follow_redirects=False)).status_code==502
    h.app.state.sso.adapter.employee=VerifiedEmployee("000123","  ")
    assert (await h.client.get("/api/v1/auth/login/sso",follow_redirects=False)).status_code==502
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(UserModel))==0
        assert await db.scalar(select(func.count()).select_from(ProjectModel))==0


@pytest.mark.asyncio
async def test_cookie_run_resume_and_get_post_sse_keep_existing_contract(planning):
    h=planning
    enable_cookie_boundary(h,employee_id=h.user["user_id"])
    me=await sign_in(h)
    headers={"X-CSRF-Token":me["csrf_token"],"Idempotency-Key":"cookie-start"}
    body={"input":{"content":[{"type":"text","text":"품질 분석"}]}}
    assert (await h.client.post(h.path,headers={"Idempotency-Key":"missing-csrf"},json=body)).status_code==403
    response=await h.client.post(h.path,headers=headers,json=body)
    assert response.status_code==202,response.text
    rid=response.json()["run_id"]
    await execute()
    waiting=(await h.client.get(h.path+"/"+rid)).json()
    assert waiting["status"]=="waiting_input"
    plan=waiting["interrupt"][0]["payload"]["plans"][0]
    approval={"run_id":rid,"resume_token":waiting["resume_token"],"command":{"resume":{
        "action":"approve_plan","plan_id":plan["plan_id"],"plan_revision":plan["plan_revision"]}}}
    headers["Idempotency-Key"]="cookie-approve"
    assert (await h.client.post(h.path,headers=headers,json=approval)).status_code==202
    await execute()
    assert (await h.client.get(h.path+"/"+rid)).json()["status"]=="success"
    stream=await asyncio.wait_for(h.client.get(h.path+"/"+rid+"/stream"),5)
    assert stream.status_code==200 and "event: interaction.resolved" in stream.text
    streamed=await asyncio.wait_for(h.client.post(h.path+"/stream",headers=headers,json=approval),5)
    assert streamed.status_code==200 and streamed.headers["x-run-id"]==rid
    assert "event: run.snapshot" in streamed.text
    snapshots = [json.loads(line[6:]) for line in streamed.text.splitlines()
                 if line.startswith("data: ") and json.loads(line[6:]).get("type") == "run.snapshot"]
    assert snapshots and all(item["run_id"] == rid == item["data"]["run_id"] for item in snapshots)
    assert all("id" not in item["data"] for item in snapshots)
    assert (await h.client.post("/api/v1/auth/logout",headers={"X-CSRF-Token":me["csrf_token"]})).status_code==204
    assert (await h.client.get(h.path+"/"+rid+"/stream")).status_code==401
    await h.app.state.sso.close()


@pytest.mark.asyncio
async def test_cookie_admin_user_list_and_deleted_detail(cookie_api):
    h=cookie_api
    me=await sign_in(h)
    assert (await h.client.get('/api/v1/users',params={'status':'all'})).status_code==403
    async with h.factory() as db:
        await UserService.bootstrap_admin(db,UserCreate(user_id='009999',user_name='Admin',role='admin'))
    from service_auth.sso.contracts import VerifiedEmployee
    h.app.state.sso.adapter.employee=VerifiedEmployee('009999','Employee Admin')
    admin=await sign_in(h)
    response=await h.client.get('/api/v1/users',params={'q':'000123'})
    assert response.status_code==200,response.text  # GET needs cookie only, no CSRF.
    assert [item['user_id'] for item in response.json()['items']]==['000123']
    assert response.headers['cache-control']=='no-store'
    assert (await h.client.delete('/api/v1/users/000123',headers={'X-CSRF-Token':admin['csrf_token']})).status_code==204
    response=await h.client.get('/api/v1/users',params={'status':'deleted'})
    assert response.status_code==200 and response.json()['items'][0]['is_active'] is False
    detail=await h.client.get('/api/v1/users/000123')
    assert detail.status_code==200 and detail.json()['delete_yn']=='Y'
    h.client.cookies.clear()
    assert (await h.client.get('/api/v1/users',headers={'X-User-Id':'009999'})).status_code==401
    assert (await h.client.get('/api/v1/users',headers={'Authorization':'Bearer 009999'})).status_code==401

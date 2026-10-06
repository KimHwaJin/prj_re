from dtest.contracts.errors import ApplicationError
"""Admin User list/detail on guarded disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from dtest.contracts.enums import DeleteYN, UserRole
from dtest.infrastructure.database.models.user_model import UserModel as User
from dtest.infrastructure.database.models.project_model import ProjectModel as Project
from dtest.application.resources.sso_users import SsoUserDirectory
from tests.api_service.test_user_identity_postgres import database_url, harness, initialize, add_user, headers
from tests.api_service.test_read_queries_postgres import trace_reads
from dtest.contracts.auth import VerifiedEmployee

SUMMARY_FIELDS = {'user_id','user_name','role','is_active','created_at','updated_at','deleted_at'}


async def sample(h, count=17, *, deleted=False):
    await initialize(h)
    stamp = datetime(2026,1,1,tzinfo=timezone.utc)
    records = []
    async with h.factory() as db:
        for index in range(count):
            uid = uuid4()
            item = User(user_id=uid,public_user_id=f'load-{index:03}',user_name=f'Load User {index:03}',
                role=UserRole.ADMIN if index%3==0 else UserRole.USER,
                delete_yn=DeleteYN.Y if deleted and index%5==0 else DeleteYN.N,
                created_at=stamp,deleted_at=stamp+timedelta(days=1) if deleted and index%5==0 else None)
            db.add(item)
            records.append(item)
        await db.commit()
    return records


@pytest.mark.asyncio
@pytest.mark.parametrize('sort',['created_at','-created_at'])
@pytest.mark.parametrize('limit',[1,7,200])
async def test_summary_pages_are_bounded_and_no_per_user_project_query(harness,sort,limit):
    h = harness
    records = await sample(h,deleted=True)
    cursor, seen = None, []
    while True:
        params = {'q':'load-','status':'all','sort':sort,'limit':limit}
        if cursor:
            params['cursor'] = cursor
        with trace_reads(h) as queries:
            response = await h.client.get('/api/v1/users',params=params,headers=headers())
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        body = response.json()
        assert set(body) == {'items','page'} and len(body['items']) <= limit
        assert len(queries) == 2  # Authentication + one User-only page.
        assert queries[-1]['rows'] <= limit+1 and len(queries[-1]['columns']) == 8
        assert not any('projects' in query['sql'] or 'count(' in query['sql'].lower() for query in queries)
        assert all(set(item) == SUMMARY_FIELDS for item in body['items'])
        assert all('csrf_token' not in item and 'default_project_id' not in item for item in body['items'])
        seen.extend(item['user_id'] for item in body['items'])
        if not body['page']['has_next']:
            assert body['page']['next_cursor'] is None
            break
        cursor = body['page']['next_cursor']
        assert cursor and len(seen) <= len(records)
    assert seen == [item.public_user_id for item in sorted(records,key=lambda item:item.user_id,reverse=sort.startswith('-'))]
    assert all(str(item.user_id) not in seen for item in records)


@pytest.mark.asyncio
async def test_default_page_and_large_history(harness):
    h = harness
    await sample(h,count=205)
    first = await h.client.get('/api/v1/users',params={'q':'load-'},headers=headers())
    assert len(first.json()['items']) == 50 and first.json()['page']['has_next']
    params={'q':'load-','limit':200}
    first=await h.client.get('/api/v1/users',params=params,headers=headers())
    assert first.status_code == 200 and len(first.json()['items']) == 200
    params['cursor']=first.json()['page']['next_cursor']
    last=await h.client.get('/api/v1/users',params=params,headers=headers())
    assert last.status_code == 200 and len(last.json()['items']) == 5
    assert last.json()['page'] == {'has_next':False,'next_cursor':None}
    assert len({item['user_id'] for item in first.json()['items']+last.json()['items']}) == 205


@pytest.mark.asyncio
async def test_literal_case_insensitive_search_role_and_status_filters(harness):
    h = harness
    records = await sample(h,deleted=True)
    async def listed(params):
        response=await h.client.get('/api/v1/users',params=params,headers=headers())
        assert response.status_code == 200,response.text
        return response.json()
    for status in ('active','deleted','all'):
        for role in (None,'admin','user'):
            params={'q':' LOAD- ','status':status}
            if role:
                params['role']=role
            items=(await listed(params))['items']
            expected=[item for item in records if (status=='all' or
                (item.delete_yn==DeleteYN.N)==(status=='active')) and (role is None or item.role.value==role)]
            assert {item['user_id'] for item in items} == {item.public_user_id for item in expected}
            assert all(item['is_active'] == (next(r for r in expected if r.public_user_id==item['user_id']).delete_yn==DeleteYN.N) for item in items)
    assert all(item['is_active'] for item in (await listed({'q':'load-'}))['items'])
    assert (await listed({'q':'missing'})) == {'items':[],'page':{'has_next':False,'next_cursor':None}}
    for name,query in [('Percent 100%','%'),('Literal_under','_'),('Path\\piece','\\'),('홍 길동','홍')]:
        async with h.factory() as db:
            public=f'literal-{uuid4().hex}'
            db.add(User(public_user_id=public,user_name=name,role=UserRole.USER))
            await db.commit()
        items=(await listed({'q':query}))['items']
        assert {item['user_id'] for item in items} == {public}
    assert (await listed({'q':'load-user-does-not-exist'}))['items'] == []
    assert (await listed({'q':'load-001'}))['items'][0]['user_id'] == 'load-001'
    for params,count in [({'q':'load-','status':'all','created_at_from':'2026-01-01T00:00:00Z'},17),
                         ({'q':'load-','status':'all','created_at_to':'2026-01-01T00:00:00Z'},0)]:
        assert len((await listed(params))['items']) == count


@pytest.mark.asyncio
async def test_authorization_and_invalid_filters(harness):
    h = harness
    await sample(h,deleted=True)
    for hdr,code in [({},401),({'Authorization':'Bearer admin'},401),(headers('missing'),401),
                     (headers('load-001'),403),(headers('load-000'),401)]:
        response=await h.client.get('/api/v1/users',params={'status':'all','role':'admin'},headers=hdr)
        assert response.status_code == code,response.text
    for params,code in [({'q':''},422),({'q':'   '},422),({'q':'x'*101},422),({'role':'owner'},422),
                        ({'status':'inactive'},422),({'limit':0},422),({'limit':201},422),
                        ({'sort':'user_name'},422),({'cursor':'broken'},400),
                        ({'created_at_from':'2026-02-01','created_at_to':'2026-01-01'},422)]:
        response=await h.client.get('/api/v1/users',params=params,headers=headers())
        assert response.status_code == code,response.text
    assert (await h.client.get('/api/v1/users/me',headers=headers())).status_code == 200
    assert (await h.client.get('/api/v1/users/load-001',headers=headers('load-001'))).status_code == 200
    assert (await h.client.get('/api/v1/users/load-002',headers=headers('load-001'))).status_code == 404


@pytest.mark.asyncio
async def test_deleted_profile_read_does_not_reactivate_or_recreate_default_project(harness):
    h=harness
    await initialize(h)
    target=await add_user(h)
    other=await add_user(h,'other')
    assert (await h.client.delete('/api/v1/users/user-a',headers=headers())).status_code == 204
    async def snapshot():
        async with h.factory() as db:
            user=await db.scalar(select(User).where(User.public_user_id=='user-a'))
            project=await db.get(Project,UUID(target['default_project_id']))
            return (user.user_id,user.delete_yn,user.deleted_at,user.role,user.updated_at,
                    project.delete_yn,project.deleted_at,await db.scalar(select(func.count()).select_from(User)),
                    await db.scalar(select(func.count()).select_from(Project)))
    before=await snapshot()
    detail=await h.client.get('/api/v1/users/USER-A',headers=headers())
    assert detail.status_code == 200,detail.text
    assert detail.headers['cache-control']=='no-store'
    body=detail.json()
    assert body['user_id']=='user-a' and body['delete_yn']=='Y' and body['deleted_at']
    assert body['default_project_id'] is None
    deleted=await h.client.get('/api/v1/users',params={'status':'deleted'},headers=headers())
    assert [item['user_id'] for item in deleted.json()['items']] == ['user-a']
    assert deleted.json()['items'][0]['is_active'] is False
    assert (await h.client.get('/api/v1/users/user-a',headers=headers(other['user_id']))).status_code==404
    assert (await h.client.get('/api/v1/users/me',headers=headers('user-a'))).status_code==401
    assert (await h.client.patch('/api/v1/users/user-a',headers=headers(),json={'role':'admin'})).status_code==404
    assert (await h.client.delete('/api/v1/users/user-a',headers=headers())).status_code==404
    assert (await h.client.post('/api/v1/users',headers=headers(),json={'user_id':'user-a','user_name':'Reuse'})).status_code==409
    directory=SsoUserDirectory(auto_register=True,session_factory=h.factory)
    with pytest.raises((HTTPException, ApplicationError)) as error:
        await directory.bind(VerifiedEmployee('user-a','SSO Name'))
    assert error.value.status_code == 403
    assert await snapshot()==before

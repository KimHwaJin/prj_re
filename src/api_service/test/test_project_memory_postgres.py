"""Bounded shared memory, owner checks, concurrent CAS and replay on scratch PG."""
import asyncio
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,func
from api_service.test.test_user_identity_postgres import database_url,harness,initialize,add_user,add_session,headers
from api_service.services.project_memory_service import ProjectMemoryService
from api_service.models.common.project_memory_model import ProjectMemoryModel,ProjectMemoryReceiptModel
from service_contracts.project_memory import MemoryConflict,MemoryLimit

async def setup(h):
    await initialize(h)
    user=await add_user(h)
    async with h.factory() as db:
        from api_service.models.common.user_model import UserModel
        uid=await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id==user['user_id']))
    return user,uid,UUID(user['default_project_id']),ProjectMemoryService(session_factory=h.factory)

def change(key='style',content='간결한 보고서',version=0,section='report_preferences'):
    return {'section':section,'key':key,'content':content,'expected_version':version}

@pytest.mark.asyncio
async def test_explicit_http_read_edit_delete_restore_and_idempotency(harness):
    h=harness;user,uid,pid,service=await setup(h)
    base=f'/api/v1/projects/{pid}/memory';auth=headers(user['user_id'])
    assert (await h.client.get(base,headers=auth)).json()['entries']==[]
    body={'content':'명시적으로 공유한 결과: 평균 109','expected_version':0}
    path=base+'/shared_findings/summary';key={**auth,'Idempotency-Key':'first'}
    first=await h.client.put(path,headers=key,json=body)
    assert first.status_code==200,first.text
    assert (await h.client.put(path,headers=key,json=body)).json()==first.json()
    assert (await h.client.put(path,headers=key,json={**body,'content':'different'})).status_code==409
    assert (await h.client.put(path,headers=auth,json={'content':'new','expected_version':0})).status_code==409
    assert (await h.client.delete(path+'?expected_version=1',headers=auth)).status_code==200
    record=(await h.client.get(base,headers=auth)).json()['entries'][0]
    assert record['is_deleted'] and record['version']==2
    # Keeping the tombstone prevents ABA (stale version=0 cannot recreate this key).
    assert (await h.client.put(path,headers=auth,json=body)).status_code==409
    restored=await h.client.put(path,headers=auth,json={'content':'명시적 수정','expected_version':2})
    assert restored.status_code==200 and restored.json()['entries'][0]['version']==3
    other=await add_user(h,'other')
    assert (await h.client.get(base,headers=headers(other['user_id']))).status_code==404
    assert (await h.client.put(path,headers=headers(other['user_id']),json=body)).status_code==404

@pytest.mark.asyncio
async def test_concurrent_same_topic_one_wins_different_topics_both_survive_and_replay(harness):
    h=harness;user,uid,pid,service=await setup(h)
    async def write(key,text,source):
        try:return await service.apply(uid,pid,[change(key,text)],source_id=source,source={'kind':'user_edit'})
        except MemoryConflict:return 'conflict'
    results=await asyncio.gather(write('same','one','a'),write('same','two','b'))
    assert sum(isinstance(r,dict) for r in results)==1 and results.count('conflict')==1
    assert all(isinstance(r,dict) for r in await asyncio.gather(write('left','left','c'),write('right','right','d')))
    replay=await service.apply(uid,pid,[change('left','left')],source_id='c',source={'kind':'user_edit'})
    assert replay['entries'][0]['version']==1
    state=await service.read(uid,pid)
    assert {e['key'] for e in state['entries']}=={'same','left','right'}
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ProjectMemoryReceiptModel))==3

@pytest.mark.asyncio
async def test_batch_atomicity_size_bound_and_soft_delete(harness):
    h=harness;user,uid,pid,service=await setup(h)
    await service.apply(uid,pid,[change()],source_id='base',source={'kind':'user_edit'})
    with pytest.raises(MemoryConflict):
        await service.apply(uid,pid,[change('new'),change(version=0)],source_id='batch',source={'kind':'user_edit'})
    assert len((await service.read(uid,pid))['entries'])==1
    for i in range(30):
        try:await service.apply(uid,pid,[change('large_'+chr(97+i),content='x'*1000)],source_id='fill'+str(i),source={'kind':'user_edit'})
        except MemoryLimit:break
    else:raise AssertionError('Memory should hit its serialized size bound')
    import json
    state=await service.read(uid,pid)
    assert len(json.dumps(state,ensure_ascii=False))<=16000
    async with h.factory() as db:
        from api_service.models.common.project_model import ProjectModel
        from api_service.core.enums import DeleteYN
        project=await db.get(ProjectModel,pid);project.delete_yn=DeleteYN.Y;await db.commit()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:await service.read(uid,pid)
    assert exc.value.status_code==404
    with pytest.raises(HTTPException):await service.apply(uid,pid,[change('forbidden')],source_id='deleted',source={'kind':'user_edit'})

@pytest.mark.asyncio
async def test_bound_source_checks_and_same_project_cross_session_read(harness):
    h=harness;user,uid,pid,service=await setup(h)
    s1=await add_session(h,user);s2=await add_session(h,user)
    from api_service.schemas.common.run_schema import RunStart
    from api_service.services.public_run_service import PublicRunService
    async def run(sid,key):
        async with h.factory() as db:
            result=await PublicRunService.create(db,uid,UUID(sid),RunStart(input={'messages':[{'role':'user','content':'test'}]}),key)
            return str(result.run_id)
    r1=await run(s1,'one');r2=await run(s2,'two')
    first=service.for_context({'user_id':str(uid),'project_id':str(pid),'session_id':s1,'run_id':r1})
    second=service.for_context({'user_id':str(uid),'project_id':str(pid),'session_id':s2,'run_id':r2})
    await first.apply([change()])
    assert (await second.read(str(pid)))['entries'][0]['content']=='간결한 보고서'
    invalid=service.for_context({'user_id':str(uid),'project_id':str(pid),'session_id':s2,'run_id':r1})
    with pytest.raises(ValueError,match='source Run'):await invalid.read(str(pid))
    with pytest.raises(ValueError,match='invocation'):await first.read(str(uuid4()))

@pytest.mark.asyncio
async def test_migration_downgrade_upgrade_preserves_existing_project(harness,database_url,tmp_path):
    h=harness;user,uid,pid,service=await setup(h)
    import os,subprocess,sys
    from pathlib import Path
    from sqlalchemy.engine import make_url
    root=Path(__file__).resolve().parents[3]
    config=tmp_path/'migration.yml'
    config.write_text('service:\n  database_url: '+database_url+'\n  checkpoint_db_uri: '+make_url(database_url).set(drivername='postgresql').render_as_string(hide_password=False)+'\n')
    env={**os.environ,'SERVICE_CONFIG_FILE':str(config),'APP_ENV':'dev','PYTHONPATH':str(root/'src')}
    for direction,revision in [('downgrade','20260930_0023'),('upgrade','head')]:
        proc=subprocess.run([sys.executable,'-m','alembic','-c','alembic.crud.ini',direction,revision],cwd=root,env=env,capture_output=True,text=True)
        assert proc.returncode==0,proc.stderr
    state=await service.read(uid,pid)
    assert state['project_id']==str(pid) and state['entries']==[]

# Complete API -> queue Worker -> graph -> model policy -> PG -> public SSE path.
from api_service.test.test_planning_api_postgres import planning,test_config,submit,execute,read

@pytest.mark.asyncio
async def test_worker_memory_write_next_session_read_and_public_events(planning,monkeypatch):
    h=planning
    import copy,json,httpx
    from dataclasses import replace
    import service_settings
    from langgraph.checkpoint.memory import InMemorySaver
    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    from agent_service.agents.analysis.planning.graph import build_planning_graph
    from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
    from agent_service.agents.analysis.tests.test_conversation_performance import model,response
    from api_service.services.agent_graph_service import runtime as graph_runtime
    quote='보고서는 원인과 다음 행동 중심으로 간결하게 작성해줘'
    real_shape=service_settings.load_settings(config={'MODEL_PROVIDER':'openai_compatible','MODEL_NAME':'test','MODEL_API_KEY':'test',
        'API_BASE_URL':'http://llm.invalid/v1','AGENT_PROJECT_MEMORY_MODE':'auto_context'},environ={}).agent
    current=service_settings.get_settings()
    monkeypatch.setattr(service_settings,'_snapshot',replace(current,agent=real_shape))
    service=ProjectMemoryService(session_factory=h.factory)
    runtime=PlanningRuntime(real_shape,project_memory_factory=service.for_context)
    seen=[]
    async def handle(request):
        body=json.loads(request.content)
        payload=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))
        memory=next(json.loads(m['content'])['memory'] for m in body['messages'] if '"reference_type": "project_memory"' in str(m['content']))
        seen.append(copy.deepcopy(memory))
        update=[{'section':'report_preferences','key':'style','content':quote,'quote':quote,'expected_version':0}] if payload['request']==quote else []
        return response({'role':'assistant','content':json.dumps({'kind':'answer','message':'간결한 원인·행동 중심 보고서 선호를 참고하겠습니다.','grounding':{'scope':'general'},'memory_updates':update,'plans':[]},ensure_ascii=False)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        selection=runtime.models.select().model_dump()
        runtime.agents[(selection['name'],selection['revision'])]=build_agent(model(client),runtime.catalog)
        graph=build_planning_graph(runtime,checkpointer=InMemorySaver());graph_runtime.override_graph(graph)
        first=await submit(h,{'input':{'content':[{'type':'text','text':quote}]}})
        assert first.status_code==202,first.text
        rid=first.json()['run_id'];await execute()
        final=await read(h,rid)
        assert final['status']=='success',final
        assert final['result']['final_response']['project_memory']['status']=='saved'
        stream=await h.client.get(h.path+'/'+rid+'/stream',headers=headers(h.user['user_id']))
        events=[json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith('data: ')]
        assert any(e['type']=='activity.completed' and e['data'].get('kind')=='project_memory' for e in events)
        assert all('memory_updates' not in json.dumps(e) for e in events)
        sid=await add_session(h,h.user);h.path=f'/api/v1/sessions/{sid}/runs'
        second=await submit(h,{'input':{'content':[{'type':'text','text':'이 프로젝트의 보고서 선호를 알려줘'}]}})
        assert second.status_code==202,second.text
        await execute();following=await read(h,second.json()['run_id'])
        assert following['status']=='success'
    assert len(seen)==2 and seen[0]['entries']==[]
    assert seen[1]['entries'][0]['content']==quote and seen[1]['entries'][0]['source']['run_id']==rid
    for session in [h.session_id,sid]:
        assert (await graph.aget_state({'configurable':{'thread_id':session}})).values.get('execution_id') is None

from api_service.test.test_short_transactions_postgres import small_pool,runtime

@pytest.mark.asyncio
async def test_memory_api_reuses_auth_session_with_one_connection(small_pool):
    h=small_pool
    path=f"/api/v1/projects/{h.user['default_project_id']}/memory/report_preferences/style"
    response=await h.client.put(path,headers=headers(h.user['user_id']),json={'content':'짧게 작성','expected_version':0})
    assert response.status_code==200,response.text
    assert (await h.client.get(path.rsplit('/',2)[0],headers=headers(h.user['user_id']))).status_code==200
    assert h.engine.pool.checkedout()==0

@pytest.mark.asyncio
async def test_memory_snapshot_releases_only_connection_before_model_wait(small_pool):
    h=small_pool
    import httpx,json
    from agent_service.context import AgentContext
    from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
    from agent_service.agents.analysis.planning.catalog import AssetCatalog
    from agent_service.agents.analysis.tests.test_conversation_performance import model,response
    from api_service.test.test_run_cleanup_postgres import enqueue
    from api_service.models.common.user_model import UserModel
    queued=await enqueue(h)
    async with h.factory() as db:
        uid=await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id==h.user['user_id']))
    pid=h.user['default_project_id']
    source=ProjectMemoryService(session_factory=h.factory).for_context({'user_id':str(uid),'project_id':pid,'session_id':h.session_id,'run_id':queued['run_id']})
    entered=asyncio.Event();release=asyncio.Event()
    async def handle(request):
        entered.set();await release.wait()
        return response({'role':'assistant','content':json.dumps({'kind':'answer','message':'일반 답변','grounding':{'scope':'general'},'plans':[],'memory_updates':[]})})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent=build_agent(model(client),AssetCatalog())
        job=asyncio.create_task(agent.ainvoke({'request':'질문'},context=AgentContext(user_id=str(uid),project_id=pid,session_id=h.session_id,project_memory=source)))
        try:
            await asyncio.wait_for(entered.wait(),3)
            assert h.engine.pool.checkedout()==0
            path=f'/api/v1/projects/{pid}/memory/report_preferences/style'
            result=await h.client.put(path,headers=headers(h.user['user_id']),json={'content':'간결하게','expected_version':0})
            assert result.status_code==200,result.text
            assert not job.done() and h.engine.pool.checkedout()==0
        finally:
            release.set();await job

@pytest.mark.asyncio
async def test_stale_worker_claim_cannot_commit_memory(runtime):
    h=runtime
    from api_service.test.test_run_cleanup_postgres import enqueue
    from api_service.core.execution_claim import bind_execution_claim
    from api_service.models.common.user_model import UserModel
    from api_service.models.common.task_model import TaskModel
    from service_contracts.execution import ExecutionNeedsRecovery
    import api_service.agent_run_worker as worker
    queued=await enqueue(h);item=await worker.claim_one()
    assert str(item.claim.run_id)==queued['run_id']
    async with h.factory() as db:
        uid=await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id==h.user['user_id']))
        task=await db.get(TaskModel,item.claim.task_id);task.lock_token=uuid4();await db.commit()
    service=ProjectMemoryService(session_factory=h.factory)
    bound=service.for_context({'user_id':str(uid),'project_id':h.user['default_project_id'],'session_id':h.session_id,'run_id':queued['run_id']})
    with bind_execution_claim(item.claim),pytest.raises(ExecutionNeedsRecovery):
        await bound.apply([change()])
    assert (await service.read(uid,h.user['default_project_id']))['entries']==[]

"""Real scratch-PG document CAS, migration, API, Worker and pool boundaries."""
import asyncio
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select
from tests.api_service.test_user_identity_postgres import database_url,harness,initialize,add_user,add_session,headers
from api_service.resources.project_memory import ProjectMemoryPolicy
from service_contracts.memory_store import receipt_namespace, memory_namespace, MEMORY_KEY, receipt_key
from api_service.infrastructure.memory_store import runtime as store_runtime
from langgraph.store.postgres import AsyncPostgresStore
import pytest_asyncio
from service_contracts.project_memory import MemoryConflict,MemoryLimit,MemoryLimits

@pytest_asyncio.fixture(autouse=True)
async def store_lifecycle():
    store_runtime.start()
    yield
    await store_runtime.shutdown()

async def setup(h):
    await initialize(h)
    user=await add_user(h)
    async with h.factory() as db:
        from api_service.models.user_model import UserModel
        uid=await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id==user['user_id']))
    return user,uid,UUID(user['default_project_id']),ProjectMemoryPolicy(session_factory=h.factory)


def change(content='간결한 보고서',version=0,section='report_preferences',old_text=''):
    return {'section':section,'old_text':old_text,'content':content,'expected_version':version}

@pytest.mark.asyncio
async def test_http_document_edit_reset_restore_replay_and_old_paths_removed(harness):
    h=harness;user,uid,pid,service=await setup(h)
    base=f'/api/v1/projects/{pid}/memory';auth=headers(user['user_id'])
    empty=(await h.client.get(base,headers=auth)).json()
    assert empty=={'schema_version':2,'project_id':str(pid),'content':'','version':0,'updated_at':None}
    body={'content':'자유로운 Markdown\n\n## 공유할 주요 발견\n명시적으로 공유한 결과: 평균 109','expected_version':0}
    key={**auth,'Idempotency-Key':'first'}
    first=await h.client.put(base,headers=key,json=body)
    assert first.status_code==200,first.text
    assert first.json()['content']==body['content'] and first.json()['version']==1
    assert (await h.client.put(base,headers=key,json=body)).json()==first.json()
    assert (await h.client.put(base,headers=key,json={**body,'content':'different'})).status_code==409
    assert (await h.client.put(base,headers=auth,json=body)).status_code==409
    unchanged=await h.client.put(base,headers=auth,json={**body,'expected_version':1})
    assert unchanged.json()['version']==1
    reset=await h.client.delete(base+'?expected_version=1',headers={**auth,'Idempotency-Key':'reset'})
    assert reset.status_code==200 and reset.json()['content']=='' and reset.json()['version']==2
    assert (await h.client.delete(base+'?expected_version=1',headers={**auth,'Idempotency-Key':'reset'})).json()==reset.json()
    assert (await h.client.put(base,headers=auth,json=body)).status_code==409
    restored=await h.client.put(base,headers=auth,json={'content':'명시적 수정','expected_version':2})
    assert restored.status_code==200 and restored.json()['version']==3
    assert (await h.client.put(base+'/report_preferences/style',headers=auth,json=body)).status_code==404
    assert (await h.client.put(base,headers=auth,json={**body,'key':'old-topic'})).status_code==422
    assert (await h.client.put(base,headers={**auth,'Idempotency-Key':'   '},json=body)).status_code==422
    other=await add_user(h,'other')
    assert (await h.client.get(base,headers=headers(other['user_id']))).status_code==404
    assert (await h.client.put(base,headers=headers(other['user_id']),json=body)).status_code==404

@pytest.mark.asyncio
async def test_concurrent_document_edits_and_patches_cannot_overwrite_other_sessions(harness):
    h=harness;user,uid,pid,service=await setup(h)
    async def write(text,source):
        try:return await service.replace(uid,pid,text,0,source_id=source)
        except MemoryConflict:return 'conflict'
    results=await asyncio.gather(write('one','a'),write('two','b'))
    assert sum(isinstance(r,dict) for r in results)==1 and results.count('conflict')==1
    original=(await service.read(uid,pid))['content']
    async def patch(section,source,version):
        try:return await service.apply(uid,pid,[change(section=section,content=section,version=version)],source_id=source,source={'kind':'user_request'})
        except MemoryConflict:return 'conflict'
    patches=await asyncio.gather(patch('background','c',1),patch('analysis_preferences','d',1))
    assert sum(isinstance(r,dict) for r in patches)==1 and patches.count('conflict')==1
    loser='background' if patches[0]=='conflict' else 'analysis_preferences'
    await patch(loser,'retry',2)
    document=await service.read(uid,pid)
    assert document['version']==3 and document['content'].startswith(original)
    assert '프로젝트 배경' in document['content'] and '분석 선호' in document['content']
    winner='analysis_preferences' if loser=='background' else 'background'
    source='d' if winner=='analysis_preferences' else 'c'
    replay=await patch(winner,source,1)
    assert replay=={'status':'saved','version':2}
    assert (await service.read(uid,pid))['version']==3

@pytest.mark.asyncio
async def test_patch_batch_atomicity_content_bound_and_soft_delete(harness):
    h=harness;user,uid,pid,service=await setup(h)
    first=await service.replace(uid,pid,'## 보고서 선호\n간결한 보고서\n',0,source_id='base')
    with pytest.raises(MemoryConflict):
        await service.apply(uid,pid,[change(section='background',version=1),change(version=1,old_text='incorrect')],source_id='batch',source={'kind':'user_request'})
    assert (await service.read(uid,pid))['content']==first['content']
    with pytest.raises(MemoryLimit):await service.replace(uid,pid,'x'*16001,1,source_id='full')
    assert (await service.read(uid,pid))['version']==1
    async with h.factory() as db:
        from api_service.models.project_model import ProjectModel
        from api_service.models.enums import DeleteYN
        project=await db.get(ProjectModel,pid);project.delete_yn=DeleteYN.Y;await db.commit()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:await service.read(uid,pid)
    assert exc.value.status_code==404
    with pytest.raises(HTTPException):await service.replace(uid,pid,'forbidden',1,source_id='deleted')

@pytest.mark.asyncio
async def test_bound_source_checks_and_same_project_cross_session_read(harness):
    h=harness;user,uid,pid,service=await setup(h)
    s1=await add_session(h,user);s2=await add_session(h,user)
    from api_service.schemas.run_schema import RunStart
    from api_service.runs.service import PublicRunService
    async def run(sid,key):
        async with h.factory() as db:
            result=await PublicRunService.create(db,uid,UUID(sid),RunStart(input={'messages':[{'role':'user','content':'test'}]}),key)
            return str(result.run_id)
    r1=await run(s1,'one');r2=await run(s2,'two')
    first=service.for_context({'user_id':str(uid),'project_id':str(pid),'session_id':s1,'run_id':r1})
    second=service.for_context({'user_id':str(uid),'project_id':str(pid),'session_id':s2,'run_id':r2})
    async with store_runtime.open_store() as store:
        await first.apply(store,[change()])
        assert '간결한 보고서' in (await second.read(store))['content']
    invalid=service.for_context({'user_id':str(uid),'project_id':str(pid),'session_id':s2,'run_id':r1})
    async with store_runtime.open_store() as store:
        with pytest.raises(ValueError,match='source Run'):await invalid.read(store)


def migration(database_url,tmp_path,direction,revision):
    import os,subprocess,sys
    from pathlib import Path
    from sqlalchemy.engine import make_url
    root=Path(__file__).resolve().parents[2]
    raw=make_url(database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    config=tmp_path/'migration.yml'
    config.write_text('database_url: '+database_url+'\nCHECKPOINT_DB_URI: '+raw+'\n')
    env={**os.environ,'SERVICE_CONFIG_FILE':str(config),'APP_ENV':'dev','PYTHONPATH':str(root/'src')}
    proc=subprocess.run([sys.executable,'-m','alembic','-c','alembic.crud.ini',direction,revision],cwd=root,env=env,capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr

@pytest.mark.asyncio
async def test_migration_downgrade_upgrade_preserves_existing_project(harness,database_url,tmp_path):
    h=harness;user,uid,pid,service=await setup(h)
    migration(database_url,tmp_path,'downgrade','20260930_0023')
    migration(database_url,tmp_path,'upgrade','head')
    document=await service.read(uid,pid)
    assert document['project_id']==str(pid) and document['content']=='' and document['version']==0

# Complete API -> queue Worker -> graph -> model policy -> PG -> public SSE path.
from tests.api_service.test_planning_api_postgres import planning,test_config,submit,execute,read

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
    from api_service.runs.runtime import runtime as graph_runtime
    quote='보고서는 원인과 다음 행동 중심으로 간결하게 작성해줘'
    real_shape=service_settings.load_settings(config={'MODEL_PROVIDER':'openai_compatible','MODEL_NAME':'test','MODEL_API_KEY':'test',
        'API_BASE_URL':'http://llm.invalid/v1','AGENT_PROJECT_MEMORY_MODE':'auto_context'},environ={}).agent
    current=service_settings.get_settings()
    monkeypatch.setattr(service_settings,'_snapshot',replace(current,agent=real_shape))
    service=ProjectMemoryPolicy(session_factory=h.factory)
    async with store_runtime.open_store() as store:
        assert isinstance(store, AsyncPostgresStore)
    runtime=PlanningRuntime(real_shape,memory_policy_factory=service.for_context,store=store)
    seen=[]
    async def handle(request):
        body=json.loads(request.content)
        payload=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))
        memory=next(json.loads(m['content'])['memory'] for m in body['messages'] if '"reference_type": "project_memory"' in str(m['content']))
        seen.append(copy.deepcopy(memory))
        update=[{'section':'report_preferences','old_text':'','content':quote,'quote':quote,'intent':'preference_change','expected_version':0}] if payload['request']==quote else []
        return response({'role':'assistant','content':json.dumps({'kind':'answer','message':'간결한 원인·행동 중심 보고서 선호를 참고하겠습니다.','grounding':{'scope':'general'},'memory_updates':update,'plans':[]},ensure_ascii=False)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        selection=runtime.models.select().model_dump()
        runtime.agents[(selection['name'],selection['revision'])]=build_agent(model(client),runtime.catalog,store=store)
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
    assert len(seen)==2 and seen[0]['content']==''
    assert quote in seen[1]['content']
    saved=(await h.client.get(f"/api/v1/projects/{h.user['default_project_id']}/memory",headers=headers(h.user['user_id']))).json()
    assert quote in saved['content'] and saved['version']==1
    saved_source=(await service.read(seen[1]['user_id'],saved['project_id']))['source']
    assert saved_source['run_id']==rid and saved_source['changes'][0]['quote']==quote
    for session in [h.session_id,sid]:
        assert (await graph.aget_state({'configurable':{'thread_id':session}})).values.get('execution_id') is None

from tests.api_service.test_short_transactions_postgres import small_pool,runtime

@pytest.mark.asyncio
async def test_memory_api_reuses_auth_session_with_one_connection(small_pool):
    h=small_pool
    path=f"/api/v1/projects/{h.user['default_project_id']}/memory"
    response=await h.client.put(path,headers=headers(h.user['user_id']),json={'content':'짧게 작성','expected_version':0})
    assert response.status_code==200,response.text
    assert (await h.client.get(path,headers=headers(h.user['user_id']))).status_code==200
    assert h.engine.pool.checkedout()==0

@pytest.mark.asyncio
async def test_memory_snapshot_releases_only_connection_before_model_wait(small_pool):
    h=small_pool
    import httpx,json
    from agent_service.context import AgentContext
    from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
    from agent_service.agents.analysis.planning.catalog import AssetCatalog
    from agent_service.agents.analysis.tests.test_conversation_performance import model,response
    from tests.api_service.test_run_cleanup_postgres import enqueue
    from api_service.models.user_model import UserModel
    queued=await enqueue(h)
    async with h.factory() as db:
        uid=await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id==h.user['user_id']))
    pid=h.user['default_project_id']
    source=ProjectMemoryPolicy(session_factory=h.factory).for_context({'user_id':str(uid),'project_id':pid,'session_id':h.session_id,'run_id':queued['run_id']})
    entered=asyncio.Event();release=asyncio.Event()
    async def handle(request):
        entered.set();await release.wait()
        return response({'role':'assistant','content':json.dumps({'kind':'answer','message':'일반 답변','grounding':{'scope':'general'},'plans':[],'memory_updates':[]})})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        async with store_runtime.open_store() as store:
            assert isinstance(store, AsyncPostgresStore)
        agent=build_agent(model(client),AssetCatalog(),store=store)
        job=asyncio.create_task(agent.ainvoke({'request':'질문'},context=AgentContext(user_id=str(uid),project_id=pid,session_id=h.session_id,project_memory_policy=source)))
        try:
            await asyncio.wait_for(entered.wait(),3)
            assert h.engine.pool.checkedout()==0
            stats=store.conn.get_stats()
            assert stats.get('requests_waiting',0)==0
            assert stats['pool_available']==stats['pool_size']
            path=f'/api/v1/projects/{pid}/memory'
            result=await h.client.put(path,headers=headers(h.user['user_id']),json={'content':'간결하게','expected_version':0})
            assert result.status_code==200,result.text
            assert not job.done() and h.engine.pool.checkedout()==0
        finally:
            release.set();await job

@pytest.mark.asyncio
async def test_stale_worker_claim_cannot_commit_memory(runtime):
    h=runtime
    from tests.api_service.test_run_cleanup_postgres import enqueue
    from api_service.runs.claim_context import bind_execution_claim
    from api_service.models.user_model import UserModel
    from api_service.models.task_model import TaskModel
    from service_contracts.execution import ExecutionNeedsRecovery
    import api_service.workers.agent as worker
    queued=await enqueue(h);item=await worker.claim_one()
    assert str(item.claim.run_id)==queued['run_id']
    async with h.factory() as db:
        uid=await db.scalar(select(UserModel.user_id).where(UserModel.public_user_id==h.user['user_id']))
        task=await db.get(TaskModel,item.claim.task_id);task.lock_token=uuid4();await db.commit()
    service=ProjectMemoryPolicy(session_factory=h.factory)
    bound=service.for_context({'user_id':str(uid),'project_id':h.user['default_project_id'],'session_id':h.session_id,'run_id':queued['run_id']})
    with bind_execution_claim(item.claim),pytest.raises(ExecutionNeedsRecovery):
        async with store_runtime.open_store() as store:
            await bound.apply(store,[change()])
    assert (await service.read(uid,h.user['default_project_id']))['content']==''


@pytest.mark.asyncio
async def test_official_store_batch_rolls_back_memory_and_receipt_on_failure(harness, monkeypatch):
    h=harness;user,uid,pid,policy=await setup(h)
    from langgraph.store.base import PutOp
    original=AsyncPostgresStore.abatch
    async def broken(self, ops):
        ops=list(ops)
        if any(isinstance(op,PutOp) for op in ops):
            await original(self,ops[:1])
            raise RuntimeError('injected failure after actual Store put')
        return await original(self,ops)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncPostgresStore,'abatch',broken)
        with pytest.raises(RuntimeError,match='injected failure'):
            await policy.apply(uid,pid,[change()],source_id='rollback',source={'kind':'user_edit'})
    assert (await policy.read(uid,pid))['content']==''
    async with store_runtime.open_store() as store:
        assert await store.asearch(receipt_namespace(uid,pid))==[]
    assert (await policy.apply(uid,pid,[change()],source_id='rollback',source={'kind':'user_edit'}))['version']==1


@pytest.mark.asyncio
async def test_migration_combines_topics_preserves_text_sources_and_unrelated_store(harness,database_url,tmp_path):
    h=harness;user,uid,pid,policy=await setup(h)
    migration(database_url,tmp_path,'downgrade','20261003_0027')
    namespace=memory_namespace(uid,pid)
    async with store_runtime.open_store() as store:
        for section,key,content,version,deleted in [
            ('background','purpose','품질 분석\n여러 줄 배경',2,False),
            ('report_preferences','audience','비전문가 대상',3,False),
            ('report_preferences','style','결론 먼저',1,False),
            ('shared_findings','deleted','제외되어야 하는 옛 결과',4,True)]:
            await store.aput((*namespace,section),key,{'section':section,'key':key,'content':content,'version':version,
                'is_deleted':deleted,'source':{'kind':'user_edit'},'updated_at':'2026-10-02T00:00:00+00:00'})
        await store.aput(receipt_namespace(uid,pid),receipt_key('old'),{'source_id':'old','digest':'old','result':{'entries':[]}})
        await store.aput(('another_application',),'keep',{'preserved':True})
        await store.aput(('dtest','projectXmemory',str(uid),str(pid),'background'),'keep',{'preserved':True})
    migration(database_url,tmp_path,'upgrade','head')
    before=await policy.read(uid,pid)
    assert before['content']=='## 프로젝트 배경\n품질 분석\n여러 줄 배경\n\n## 보고서 선호\n비전문가 대상\n\n결론 먼저\n'
    assert before['version']==10 and len(before['source']['previous_sources'])==4
    async with store_runtime.open_store() as store:
        assert len(await store.asearch(namespace))==1
        assert await store.asearch(receipt_namespace(uid,pid))==[]
        assert (await store.aget(('another_application',),'keep')).value=={'preserved':True}
        assert (await store.aget(('dtest','projectXmemory',str(uid),str(pid),'background'),'keep')).value=={'preserved':True}
    # Marked downgrade/upgrade preserves arbitrary document text exactly.
    migration(database_url,tmp_path,'downgrade','20261003_0027')
    migration(database_url,tmp_path,'upgrade','head')
    assert await policy.read(uid,pid)==before
    result=await policy.replace(uid,pid,'## 자유 형식\n임의의 새 문서\n',10,source_id='after-migration')
    migration(database_url,tmp_path,'downgrade','20261003_0027')
    migration(database_url,tmp_path,'upgrade','head')
    assert (await policy.read(uid,pid))['content']==result['content']

@pytest.mark.asyncio
async def test_configured_patch_limit_replay_and_manual_document_limit(harness):
    h=harness;user,uid,pid,service=await setup(h)
    custom=ProjectMemoryPolicy(session_factory=h.factory,limits=MemoryLimits(patch_max_chars=6000,max_chars=30000,max_updates=2))
    patches=[change(section='background',content='x'*5000),change(content='y'*5000)]
    first=await custom.apply(uid,pid,patches,source_id='two',source={'kind':'user_request'})
    assert await service.apply(uid,pid,patches,source_id='two',source={'kind':'user_request'})==first
    with pytest.raises(MemoryLimit):
        await service.apply(uid,pid,[change(content='z'*5000,old_text='y'*5000,version=1)],source_id='large-patch',source={'kind':'user_request'})
    # Manual PUT is bounded by document length, not the Agent patch length.
    assert (await service.replace(uid,pid,'manual '*1000,1,source_id='manual'))['version']==2

@pytest.mark.asyncio
async def test_lowered_limits_preserve_reads_and_allow_gradual_shrinking(harness):
    h=harness;user,uid,pid,service=await setup(h)
    await service.replace(uid,pid,'x'*2400,0,source_id='large')
    smaller=ProjectMemoryPolicy(session_factory=h.factory,limits=MemoryLimits(max_chars=1024,patch_max_chars=1000))
    assert len((await smaller.read(uid,pid))['content'])==2400
    with pytest.raises(MemoryLimit):await smaller.replace(uid,pid,'x'*2401,1,source_id='grow')
    await smaller.replace(uid,pid,'x'*1800,1,source_id='shrink')
    await smaller.replace(uid,pid,'short',2,source_id='shrink-again')
    assert (await smaller.read(uid,pid))['content']=='short'

@pytest.mark.asyncio
async def test_reset_blocks_stale_agent_but_new_request_can_start_new_memory(harness):
    h=harness;user,uid,pid,service=await setup(h)
    await service.apply(uid,pid,[change()],source_id='base',source={'kind':'user_request'})
    await service.reset(uid,pid,1,source_id='reset')
    with pytest.raises(MemoryConflict):
        await service.apply(uid,pid,[change(version=1,old_text='간결한 보고서',content='과거 선호 복원')],source_id='stale',source={'kind':'user_request'})
    assert (await service.read(uid,pid))['content']==''
    await service.apply(uid,pid,[change(version=2,content='새 요청으로 만든 선호')],source_id='new-request',source={'kind':'user_request'})
    assert (await service.read(uid,pid))['version']==3

@pytest.mark.asyncio
async def test_normalized_section_keeps_exact_current_quote_as_provenance(harness):
    h=harness;user,uid,pid,service=await setup(h)
    sid=await add_session(h,user)
    from api_service.schemas.run_schema import RunStart
    from api_service.runs.service import PublicRunService
    async with h.factory() as db:
        run=await PublicRunService.create(db,uid,UUID(sid),RunStart(input={'messages':[{'role':'user','content':'앞으로 보고서는 비전문가를 대상으로 작성해줘'}]}),'normalized')
        rid=str(run.run_id)
    bound=service.for_context({'user_id':str(uid),'project_id':str(pid),'session_id':sid,'run_id':rid})
    update={**change(content='보고서 독자는 비전문가'),'quote':'앞으로 보고서는 비전문가를 대상으로 작성해줘','intent':'preference_change'}
    async with store_runtime.open_store() as store:
        first=await bound.apply(store,[update]);assert await bound.apply(store,[update])==first
    saved=await service.read(uid,pid)
    assert '보고서 독자는 비전문가' in saved['content']
    assert saved['source']['changes'][0]['quote']==update['quote']
    assert saved['source']['run_id']==rid

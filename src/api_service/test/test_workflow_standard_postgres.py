"""Opt-in isolated PostgreSQL: directly authored Workflow 2.0 API lifecycle."""
from copy import deepcopy
import pytest
from sqlalchemy import select

from api_service.test.test_planning_api_postgres import test_config, planning
from api_service.test.test_user_identity_postgres import headers, add_user
from agent_service.agents.analysis.tests.asset_fixtures import assets
from agent_service.agents.analysis.tests.test_workflow_standard import public_document
from api_service.models.common.workflow_model import WorkflowEmbeddingModel
from api_service.services.workflow_file_store import WorkflowFileStore

@pytest.mark.asyncio
async def test_direct_author_update_conflict_clone_promote_visibility_and_delete(planning,tmp_path,monkeypatch):
    h=planning;catalog,case=assets(tmp_path/'assets','inventory')
    import api_service.services.workflow_service as service
    monkeypatch.setattr(service,'deployed_analysis_assets',lambda:catalog)
    monkeypatch.setattr(WorkflowFileStore,'_root',staticmethod(lambda:tmp_path/'workflows'))
    auth=headers(h.user['user_id']);doc=public_document(case,adaptive=True)
    response=await h.client.post('/api/v1/workflows',headers=auth,json={'user_queries':['Calculate approved totals'], 'document':doc,'tags':['TEST']})
    assert response.status_code==201,response.text
    resource=response.json();wid=resource['workflow_id'];old_path=resource['file_path'];old_sha=resource['content_sha256']
    assert resource['source_run_id'] is None and resource['schema_version']=='2.0'
    assert resource['document']==doc
    # Distinct direct authoring requests must not collapse through source_run_id=NULL.
    second=await h.client.post('/api/v1/workflows',headers=auth,json={'user_queries':['Calculate approved totals'], 'document':doc})
    assert second.status_code==201 and second.json()['workflow_id']!=wid
    other=await add_user(h,name="workflow-other")
    assert (await h.client.get('/api/v1/workflows/'+wid,headers=headers(other['user_id']))).status_code==404
    path='/api/v1/workflows/'+wid
    altered=deepcopy(doc);altered['workflow']['name']='Updated definition'
    missing=await h.client.patch(path,headers=auth,json={'document':altered})
    assert missing.status_code==422
    # Old embedding cannot remain active after a definition change.
    async with h.factory() as db:
        from uuid import UUID
        db.add(WorkflowEmbeddingModel(workflow_id=UUID(wid),embedded_text='old',embedded_text_sha256='a'*64,
            model_provider='test',model_name='test',dimensions=1,vector_values=[1.0],status='ready',is_active=True))
        await db.commit()
    edited=await h.client.patch(path,headers=auth,json={'document':altered,'expected_content_sha256':old_sha})
    assert edited.status_code==200,edited.text
    current=edited.json()
    assert (await h.client.patch(path,headers=auth,json={'tags':['test']})).status_code==200
    assert (await h.client.patch(path,headers=auth,json={'tags':None})).status_code==200
    assert current['name']=='Updated definition' and current['document']==altered
    assert current['file_path']!=old_path and WorkflowFileStore.read(old_path)==doc
    assert (await h.client.patch(path,headers=auth,json={'document':doc,'expected_content_sha256':old_sha})).status_code==409
    async with h.factory() as db:
        row=await db.scalar(select(WorkflowEmbeddingModel))
        assert not row.is_active and row.status=='superseded'
    cloned=await h.client.post(path+'/clone',headers=auth,json={'name':'Clone name'})
    assert cloned.status_code==201 and cloned.json()['document']['workflow']['name']=='Clone name'
    promoted=await h.client.post(path+'/promote',headers=auth)
    assert promoted.status_code==201,promoted.text
    template=promoted.json();assert template['lifecycle']=='template' and template['is_recommendable']
    assert (await h.client.get('/api/v1/workflows/'+template['workflow_id'],headers=headers(other['user_id']))).status_code==200
    assert (await h.client.patch(path,headers=headers(other['user_id']),json={'tags':['wrong']})).status_code in {403,404}
    assert (await h.client.delete(path,headers=auth)).status_code==204
    assert (await h.client.get(path,headers=auth)).status_code==404
    assert WorkflowFileStore.read(old_path)==doc

@pytest.mark.asyncio
async def test_invalid_original_and_invalid_catalog_reference_rejected(planning,tmp_path,monkeypatch):
    h=planning;catalog,case=assets(tmp_path/'assets','billing')
    import api_service.services.workflow_service as service
    monkeypatch.setattr(service,'deployed_analysis_assets',lambda:catalog)
    monkeypatch.setattr(WorkflowFileStore,'_root',staticmethod(lambda:tmp_path/'workflows'))
    doc=public_document(case);doc['workflow']['steps'][0]['tools'][1]['arguments'][case['object_arg']]['output']='unknown'
    invalid=await h.client.post('/api/v1/workflows',headers=headers(h.user['user_id']),json={'user_queries':['Calculate approved totals'], 'document':doc})
    assert invalid.status_code==422 and 'Unknown registered output' in invalid.text
    doc['workflow_version']='1.0'
    original=await h.client.post('/api/v1/workflows',headers=headers(h.user['user_id']),json={'user_queries':['Calculate approved totals'], 'document':doc})
    assert original.status_code==422 and 'explicitly migrated' in original.text
    assert not (tmp_path/'workflows').exists()


@pytest.mark.asyncio
async def test_concurrent_content_updates_accept_one_revision_only(planning,tmp_path,monkeypatch):
    import asyncio
    h=planning;catalog,case=assets(tmp_path/'assets','inventory')
    import api_service.services.workflow_service as service
    monkeypatch.setattr(service,'deployed_analysis_assets',lambda:catalog)
    monkeypatch.setattr(WorkflowFileStore,'_root',staticmethod(lambda:tmp_path/'workflows'))
    auth=headers(h.user['user_id']);doc=public_document(case)
    created=await h.client.post('/api/v1/workflows',headers=auth,json={'user_queries':['Calculate approved totals'], 'document':doc})
    row=created.json();path='/api/v1/workflows/'+row['workflow_id']
    alternatives=[]
    for name in ['Concurrent A','Concurrent B']:
        alternative=deepcopy(doc);alternative['workflow']['name']=name;alternatives.append(alternative)
    replies=await asyncio.gather(*(h.client.patch(path,headers=auth,json={'document':d,'expected_content_sha256':row['content_sha256']}) for d in alternatives))
    assert sorted(r.status_code for r in replies)==[200,409]
    accepted=next(r.json() for r in replies if r.status_code==200)
    latest=await h.client.get(path,headers=auth)
    assert latest.json()['document']==accepted['document']
    assert WorkflowFileStore.read(row['file_path'])==doc


@pytest.mark.asyncio
async def test_failed_db_commit_preserves_previous_document(planning,tmp_path,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    h=planning;catalog,case=assets(tmp_path/'assets','billing')
    import api_service.services.workflow_service as service
    monkeypatch.setattr(service,'deployed_analysis_assets',lambda:catalog)
    monkeypatch.setattr(WorkflowFileStore,'_root',staticmethod(lambda:tmp_path/'workflows'))
    auth=headers(h.user['user_id']);doc=public_document(case)
    created=await h.client.post('/api/v1/workflows',headers=auth,json={'user_queries':['Calculate approved totals'], 'document':doc})
    row=created.json();path='/api/v1/workflows/'+row['workflow_id']
    replacement=deepcopy(doc);replacement['workflow']['name']='Rolled back'
    async def fail_commit(self):raise RuntimeError('Injected commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession,'commit',fail_commit)
        with pytest.raises(RuntimeError,match='Injected commit failure'):
            await h.client.patch(path,headers=auth,json={'document':replacement,'expected_content_sha256':row['content_sha256']})
    latest=await h.client.get(path,headers=auth)
    assert latest.json()['document']==doc and latest.json()['file_path']==row['file_path']
    assert WorkflowFileStore.read(row['file_path'])==doc
    assert len(list((tmp_path/'workflows').glob('*.json')))==1


@pytest.mark.asyncio
async def test_rollback_reusing_historical_revision_never_deletes_it(planning,tmp_path,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    h=planning;catalog,case=assets(tmp_path/'assets','inventory')
    import api_service.services.workflow_service as service
    monkeypatch.setattr(service,'deployed_analysis_assets',lambda:catalog)
    monkeypatch.setattr(WorkflowFileStore,'_root',staticmethod(lambda:tmp_path/'workflows'))
    auth=headers(h.user['user_id']);doc=public_document(case)
    created=await h.client.post('/api/v1/workflows',headers=auth,json={'user_queries':['Calculate approved totals'], 'document':doc})
    old=created.json();path='/api/v1/workflows/'+old['workflow_id']
    changed=deepcopy(doc);changed['workflow']['name']='Second revision'
    edited=await h.client.patch(path,headers=auth,json={'document':changed,'expected_content_sha256':old['content_sha256']})
    current=edited.json()
    async def fail_commit(self):raise RuntimeError('Injected reused-revision failure')
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession,'commit',fail_commit)
        with pytest.raises(RuntimeError,match='reused-revision'):
            await h.client.patch(path,headers=auth,json={'document':doc,'expected_content_sha256':current['content_sha256']})
    latest=await h.client.get(path,headers=auth)
    assert latest.json()['content_sha256']==current['content_sha256']
    assert WorkflowFileStore.read(old['file_path'])==doc
    assert WorkflowFileStore.read(current['file_path'])==changed
    assert len(list((tmp_path/'workflows').glob('*.json')))==2

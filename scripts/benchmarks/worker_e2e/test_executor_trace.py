"""Diagnostic traces distinguish transport errors from accepted-POST graph errors."""
import importlib.util
import json
from pathlib import Path
from dataclasses import replace
import httpx
import pytest

from agent_config import load_agent_settings
from integrations.executor.client import ExecutorClient, ExecutorOutcomeUnknown, submission_scope

spec=importlib.util.spec_from_file_location('diagnostic_executor_trace',Path(__file__).with_name('executor_trace.py'))
trace=importlib.util.module_from_spec(spec);spec.loader.exec_module(trace)


def settings():
    return load_agent_settings({'EXECUTOR_BASE_URL':'http://fixture.test','EXECUTOR_SUBMIT_ENABLED':'true'})


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['409','response_lost','checkpoint_failure'])
async def test_status_and_original_cause_preserved_without_payloads(monkeypatch,mode):
    metrics={};trace.install(metrics,lambda:True,patch=monkeypatch.setattr)
    secret='never-store-source-or-key'
    def transport(request):
        if mode=='response_lost':raise httpx.ReadError(secret)
        return httpx.Response(409 if mode=='409' else 202,json={'execution_id':'e','state':{'status':'QUEUED','version':2},'operation':{},'private':secret})
    token=trace.command.set({'command_id':'c','event_sequence':49})
    try:
        async with ExecutorClient(settings(),transport=httpx.MockTransport(transport)) as client:
            with pytest.raises(ExecutorOutcomeUnknown) as error:
                with submission_scope():
                    await client.request('POST','http://fixture.test/api/v1/executions/e/operations',{'idempotency_key':secret,'code':secret})
                    if mode=='checkpoint_failure':raise RuntimeError(secret)
            trace.failed(metrics,error.value)
    finally:trace.command.reset(token)
    row=metrics['executor_requests'][0]
    assert row['command_id']=='c' and row['event_sequence']==49
    assert row['status_code']==(None if mode=='response_lost' else 409 if mode=='409' else 202)
    root=metrics['execution_errors'][0]['exception_chain'][-1]['type']
    assert root==('ReadError' if mode=='response_lost' else 'RuntimeError' if mode=='checkpoint_failure' else 'ExecutorOutcomeUnknown')
    assert row['error'] is None if mode=='checkpoint_failure' else row['error']=='ExecutorOutcomeUnknown'
    assert secret not in json.dumps(metrics)


@pytest.mark.asyncio
async def test_concurrent_commands_do_not_share_trace_identity(monkeypatch):
    import asyncio
    metrics={};trace.install(metrics,lambda:True,patch=monkeypatch.setattr)
    async def transport(request):
        await asyncio.sleep(.001)
        return httpx.Response(202,json={'execution_id':request.url.path.rsplit('/',1)[-1]})
    async with ExecutorClient(settings(),transport=httpx.MockTransport(transport)) as client:
        async def invoke(i):
            token=trace.command.set({'command_id':str(i)})
            try:await client.request('POST',f'http://fixture.test/executions/{i}',{'idempotency_key':str(i)})
            finally:trace.command.reset(token)
        await asyncio.gather(*(invoke(i) for i in range(20)))
    assert len(metrics['executor_requests'])==20
    assert all(row['command_id']==row['receipt']['execution_id'] for row in metrics['executor_requests'])

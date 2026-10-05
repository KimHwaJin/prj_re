"""Observe final model messages without altering responses or the middleware chain."""
import importlib.util
import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage


@pytest.mark.asyncio
async def test_observer_records_final_model_boundary_and_preserves_the_call(monkeypatch):
    directory=Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(directory))
    spec=importlib.util.spec_from_file_location('scope_diagnostic',directory/'verify_real_model_parameters.py')
    diagnostic=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    from langchain_openai.chat_models.base import BaseChatOpenAI
    from agent_service.middleware.prompt_json import PromptJsonMiddleware
    from integrations.executor.client import ExecutorClient
    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    # observe is process-local in the CLI; restore all its hooks for this test.
    for cls,name in ((BaseChatOpenAI,'_agenerate'),(PromptJsonMiddleware,'awrap_model_call'),
                     (ExecutorClient,'request'),(PlanningRuntime,'execution_role')):
        monkeypatch.setattr(cls,name,getattr(cls,name))
    calls=[]
    response=object()
    async def original(self,messages,*args,**kwargs):
        calls.append((self,messages,args,kwargs))
        return response
    monkeypatch.setattr(BaseChatOpenAI,'_agenerate',original)
    report={'model_calls':[],'model_inputs':[],'executor_calls':[],'execution_roles':[]}
    active={'name':'followup'}
    diagnostic.observe(report,active)
    messages=[HumanMessage(content='current request'),HumanMessage(content=json.dumps({
        'reference_type':'previous_completed_session_analysis',
        'analysis':{'execution_scope':{'steps':[{'arguments':{'columns':{'value':['max_val']}}}]}}}))]
    caller=object()
    result=await BaseChatOpenAI._agenerate(caller,messages,stop=['stop'])
    assert result is response and calls==[(caller,messages,(),{'stop':['stop']})]
    assert report['model_inputs']==[{'scenario':'followup','human_inputs':[m.content for m in messages]}]
    assert report['model_calls']==[]

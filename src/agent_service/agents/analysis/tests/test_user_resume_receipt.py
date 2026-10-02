"""Production HITL nodes keep private resume identity out of business payloads."""
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from devtools.analysis.runtime import local_runtime, local_input
from agent_service.agents.analysis.planning.graph import build_planning_graph
from service_contracts.user_resume import resume_envelope, resume_identity
from service_contracts.initial_request import initial_identity


@pytest.mark.asyncio
@pytest.mark.parametrize("with_initial_identity", [False, True])
async def test_actual_analysis_hitl_nodes_persist_each_addressed_receipt(tmp_path, with_initial_identity):
    runtime=local_runtime()
    graph=build_planning_graph(runtime,checkpointer=InMemorySaver())
    inputs=local_input(runtime,'analysis')
    inputs.pop('initial_request_identity')
    config={'configurable':{'thread_id':inputs['session_id']}}
    if with_initial_identity:
        inputs['run_id'] = str(uuid4())
        inputs['initial_request_identity'] = initial_identity(inputs)
    result = await graph.ainvoke(inputs, config, durability='sync')
    saved = await graph.aget_state(config)
    if with_initial_identity:
        assert saved.values['run_id'] == inputs['run_id']
        assert saved.values['initial_request_receipt'] == inputs['initial_request_identity']
    else:
        assert saved.values.get('initial_request_receipt') is None
    for action in ('edit_plan','approve_plan'):
        plan=result['plan_views'][0]
        answer={'resume':{'action':action,'plan_id':plan['plan_id'],'plan_revision':plan['plan_revision']}}
        target=result['__interrupt__'][0].id
        identity=resume_identity(str(uuid4()),target,answer)
        result=await graph.ainvoke(Command(resume={target:resume_envelope(identity,answer)}),config,durability='sync')
        saved=await graph.aget_state(config)
        assert saved.values['user_resume_receipt']==identity
    assert result['final_response']['status']=='plan_approved'
    assert not (await graph.aget_state(config)).next
    assert '__user_resume_v1__' not in str(result['history'])

from types import SimpleNamespace
from uuid import UUID,uuid4
from copy import deepcopy
from sqlalchemy import select
import pytest
from api_service.services.plan_event_persistence import persist_plan_events
from api_service.models.common.agent_run_log_model import AgentRunLogModel
from api_service.test.test_projection_roundtrips_postgres import count_db,uid
from api_service.test.test_user_identity_postgres import database_url,harness
from api_service.test.test_run_cleanup_postgres import runtime,enqueue
@pytest.mark.asyncio
async def test_identical_public_projection(runtime):
 h=runtime;rid=UUID((await enqueue(h))['run_id']);context=SimpleNamespace(user_id=await uid(h),session_id=h.session_id)
 state={'public_events':[{'event_id':str(uuid4()),'owner_run_id':str(rid),'envelope':{'schema_version':1,'type':'activity.updated','sequence':1,'session_id':h.session_id,'run_id':str(rid),'occurred_at':'2026-10-04T00:00:00+00:00','data':{'title':'Original'}}} for _ in range(3)]}
 with count_db(h,'public_batch_new') as count:
  async with h.factory() as db:first=await persist_plan_events(db,state,context)
 changed=deepcopy(state)
 for item in changed['public_events']:item['envelope']['data']['title']='Changed retry'
 with count_db(h,'public_batch_replay') as count:
  async with h.factory() as db:repeated=await persist_plan_events(db,changed,context)
 assert first==repeated
 async with h.factory() as db:
  logs=list(await db.scalars(select(AgentRunLogModel).where(AgentRunLogModel.run_id==rid)))
  assert len(logs)==3 and all(log.payload['data']['title']=='Original' for log in logs)

"""Ownership/storage deferrals never consume event business retry attempts."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.execution_lifecycle import ExecutionNeedsRecovery
from app.worker.contracts import DeferEvent
from app.worker.consumer import AckDecision, StreamMessage
from app.worker.dispatcher import Dispatcher


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[DeferEvent('busy'),ExecutionNeedsRecovery('lost'),SQLAlchemyError('unavailable')])
async def test_session_failure_defers_without_incrementing_business_attempts(error):
    command_id=uuid4()
    row={'session_id':str(uuid4()),'generation':1,'state':'READY','failure_attempts':0}
    context=SimpleNamespace(event=SimpleNamespace(event_type='completed'))
    store=SimpleNamespace(namespace='test',command=AsyncMock(return_value=row),
                          context=lambda _:context,set_state=AsyncMock())
    @asynccontextmanager
    async def hold(_):
        yield
    dispatcher=Dispatcher(store,SimpleNamespace(hold=hold),{'completed':AsyncMock(side_effect=error)})
    result=await dispatcher.handle(StreamMessage('1-0',{
        'schema_version':'1','namespace':'test','command_id':str(command_id),'generation':'1',
    }))
    assert result.decision==AckDecision.DEFER
    store.set_state.assert_awaited_once_with(command_id,'RUNNING')

"""Disconnect may cancel the subscriber, never abandon a partially acquired frame."""
import asyncio
from types import SimpleNamespace
import pytest
from api_service.services.run_stream_service import RunStreamHub

@pytest.mark.asyncio
async def test_cancel_waits_for_owned_frame_to_finish_before_propagating(monkeypatch):
    hub=RunStreamHub(SimpleNamespace())
    entered=asyncio.Event();finish=asyncio.Event();closed=asyncio.Event();calls=0
    async def read_frame(entry,sequence):
        nonlocal calls
        calls+=1;entered.set()
        try:await finish.wait();return 'frame',1
        finally:closed.set()
    monkeypatch.setattr(hub,'_read_frame',read_frame)
    subscriber=asyncio.create_task(hub.read(object(),0));await entered.wait()
    subscriber.cancel();await asyncio.sleep(0);subscriber.cancel();await asyncio.sleep(0)
    assert not subscriber.done() and not closed.is_set()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(subscriber,1)
    assert closed.is_set() and calls==1

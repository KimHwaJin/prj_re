"""Request metadata must not add a task boundary to a streaming response."""

import asyncio
import pytest
from dtest.api_service.middleware.request_id import RequestIdMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing,header",
    [(None, None), (None, "caller-id"), ("platform-id", "caller-id")],
)
async def test_request_id_preserves_platform_and_runs_in_same_task(
    existing, header
):
    origin = asyncio.current_task()
    messages = []
    scope = {
        "type": "http",
        "state": {"request_id": existing} if existing else {},
        "headers": [(b"x-request-id", header.encode())] if header else [],
    }

    async def app(scope, receive, send):
        assert asyncio.current_task() is origin
        assert scope["state"]["request_id"]
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"data: snapshot\n\n",
                "more_body": True,
            }
        )
        raise asyncio.CancelledError

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(asyncio.CancelledError):
        await RequestIdMiddleware(app)(scope, receive, send)
    value = dict(messages[0]["headers"])[b"x-request-id"].decode()
    assert value == (existing or header or scope["state"]["request_id"])
    assert len(messages) == 2

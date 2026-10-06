"""Pure ASGI request ID propagation, including early SSE disconnects."""

from uuid import uuid4
from starlette.datastructures import Headers, MutableHeaders


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})
        if not state.get("request_id"):
            state["request_id"] = (
                Headers(scope=scope).get("X-Request-ID")
                or f"req_{uuid4().hex}"
            )

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = state[
                    "request_id"
                ]
            await send(message)

        await self.app(scope, receive, send_with_id)

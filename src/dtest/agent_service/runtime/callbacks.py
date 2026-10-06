"""Translate LangChain callback events into the injected service token sink."""

from langchain_core.callbacks import AsyncCallbackHandler


class TokenCallbacks(AsyncCallbackHandler):
    run_inline = True
    raise_error = True

    def __init__(self, sink):
        self.sink = sink

    async def on_llm_new_token(self, *args, **kwargs):
        return await self.sink.on_llm_new_token(*args, **kwargs)

"""Native async BaseChatModel doubles and common factory test builders."""
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from dtest.agent_service.factory import build_role_agent, text_output, json_output
from dtest.agent_service.middleware import ProjectPromptMiddleware

class AsyncTestChatModel(BaseChatModel):
    backend: Any
    @property
    def _llm_type(self): return "async-test"
    def _generate(self, *args, **kwargs): raise AssertionError("sync model path used")
    async def _agenerate(self, messages, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=await self.backend.ainvoke(messages))])
    def bind_tools(self, tools, **kwargs): return self.bind(**kwargs)

def chat_model(model):
    return model if isinstance(model, BaseChatModel) else AsyncTestChatModel(backend=model)

def text_agent(model, system_prompt):
    return build_role_agent(chat_model(model), name="test_text", system_prompt=system_prompt,
        tools=[], middleware=[ProjectPromptMiddleware()], decode=text_output("answer"), input_key="user_request")

def structured_agent(model, system_prompt, output_type, method="prompt_json", max_validation_attempts=3):
    return build_role_agent(chat_model(model), name="test_json", system_prompt=system_prompt,
        tools=[], middleware=[ProjectPromptMiddleware()], output_type=output_type,
        structured_output_mode=method, max_validation_attempts=max_validation_attempts, decode=json_output(output_type))

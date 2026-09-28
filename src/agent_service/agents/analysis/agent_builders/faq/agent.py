"""Declare faq: independent prompt, text response, tools and middleware."""
from agent_service.factory import build_role_agent, text_output
from agent_service.middleware import ProjectPromptMiddleware
from .._prompts import load_prompt

def build_agent(model):
    return build_role_agent(
        model,
        name="faq",
        system_prompt=load_prompt(__package__),
        tools=[],
        middleware=[ProjectPromptMiddleware()],
        decode=text_output("answer"),
        input_key='user_request',
    )

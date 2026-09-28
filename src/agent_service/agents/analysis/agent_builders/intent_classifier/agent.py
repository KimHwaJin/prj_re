"""Declare intent_classifier: independent prompt, labels, tools and middleware."""
from agent_service.factory import build_role_agent, label_output
from agent_service.middleware import ProjectPromptMiddleware
from ...schemas.agents.orchestration_schema import AnalysisIntentOutput
from .._prompts import load_prompt

LABELS = ('failure_prediction', 'root_cause', 'data_drift')

def build_agent(model):
    prompt = load_prompt(__package__) + "\n\nClassify the request and return exactly one label from the list below. Return only the label, with no JSON, markdown, reason, or other text.\n" + "\n".join(LABELS)
    return build_role_agent(
        model,
        name="intent_classifier",
        system_prompt=prompt,
        tools=[],
        middleware=[ProjectPromptMiddleware()],
        decode=label_output(AnalysisIntentOutput, "intent", LABELS),
    )

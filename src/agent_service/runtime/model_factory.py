"""Construct a configured model without importing analysis roles or graphs."""
from typing import Any
from agent_config import AgentSettings


def create_chat_model(settings: AgentSettings) -> Any:
    """Create the configured OpenAI-compatible model."""
    if settings.model_provider == "openai_compatible":
        from agent_service.model import CompatibleChatOpenAI

        if not settings.model_api_key or not settings.api_base_url:
            raise ValueError("Internal deployment requires MODEL_API_KEY and API_BASE_URL")
        model_kwargs: dict[str, Any] = {}
        if settings.model_enable_thinking is not None:
            model_kwargs["extra_body"] = {
                "chat_template_kwargs": {
                    "enable_thinking": settings.model_enable_thinking,
                }
            }
        return CompatibleChatOpenAI(
            api_key=settings.model_api_key,
            base_url=settings.api_base_url,
            model=settings.model_name,
            temperature=settings.model_temperature,
            timeout=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            **model_kwargs,
        )

    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider!r}")

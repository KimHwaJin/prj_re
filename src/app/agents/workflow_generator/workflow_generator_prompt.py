from pathlib import Path


_PROMPT_PATH = Path(__file__).with_name("workflow_generator_prompt.md")

WORKFLOW_GENERATOR_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

__all__ = ["WORKFLOW_GENERATOR_PROMPT"]

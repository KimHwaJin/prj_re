"""Report generator agent construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MarkdownReportAgent:
    model: Any
    system_prompt: str

    def invoke(self, payload: Any) -> dict[str, str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        response = self.model.invoke(
            [
                SystemMessage(content=self.system_prompt),
                HumanMessage(
                    content=json.dumps(payload, ensure_ascii=False, default=str)
                ),
            ]
        )
        content = response.content if hasattr(response, "content") else response
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Report generator must return non-empty markdown text")
        return {"content": content.strip()}


def create_report_generator_agent(model: Any):
    prompt_path = Path(__file__).with_name("report_generator_prompt.md")
    return MarkdownReportAgent(
        model=model,
        system_prompt=prompt_path.read_text(encoding="utf-8"),
    )


__all__ = ["MarkdownReportAgent", "create_report_generator_agent"]

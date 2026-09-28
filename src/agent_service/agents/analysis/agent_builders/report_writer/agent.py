"""Report generator agent construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .._prompts import load_prompt


@dataclass
class MarkdownReportAgent:
    model: Any
    system_prompt: str

    async def ainvoke(self, payload: Any) -> dict[str, str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        response = await self.model.ainvoke(
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


def build_agent(model: Any) -> MarkdownReportAgent:
    return MarkdownReportAgent(
        model=model,
        system_prompt=load_prompt(__package__),
    )


__all__ = ["MarkdownReportAgent", "build_agent"]

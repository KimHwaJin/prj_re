"""Stored Workflow JSON recommendation service.

Implement database vector search in this class when persistence is available.
The graph node handles an empty result by routing to Workflow generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowRecommender:
    """Return a stored Workflow recommendation, or None when no match exists."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def ainvoke(self, payload: dict[str, Any], *, context=None) -> dict[str, Any] | None:
        self.calls.append(payload)
        # TODO(INTEGRATION): 저장된 Workflow JSON 벡터 유사도 검색 연동
        return None


__all__ = ["WorkflowRecommender"]

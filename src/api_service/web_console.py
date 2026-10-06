"""Serve the packaged functional console on the existing service application."""
from __future__ import annotations

import asyncio
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse


@lru_cache(maxsize=1)
def _template() -> str:
    return (Path(__file__).parent / "static/demo.html").read_text(encoding="utf-8")


async def render_console(public_runtime: dict[str, Any]) -> HTMLResponse:
    """Paths and public mode labels only; authentication stays on normal APIs."""
    template = await asyncio.to_thread(_template)
    runtime = json.dumps(public_runtime, ensure_ascii=True).replace("<", "\\u003c")
    marker = '<script id="console-app">'
    if template.count(marker) != 1:
        raise RuntimeError("Expected one console controller")
    content = template.replace(marker, "<script>window.TEST_CONSOLE_CONFIG=" + runtime
                               + ";</script>\n" + marker)
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})

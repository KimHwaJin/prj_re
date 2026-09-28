"""HTTP client helpers for submitting execution requests to Executor."""

from __future__ import annotations

import json
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agent_config import AgentSettings
from app.schemas.agents.orchestration_schema import ExecutorSubmitResponse


class ExecutorSubmitError(RuntimeError):
    """Raised when Executor does not accept an execution request."""


def _format_execution_url(path_template: str, execution_id: str) -> str:
    if not execution_id.strip():
        raise ExecutorSubmitError("execution_id must be a non-empty string")
    return path_template.format(execution_id=execution_id)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    ssl_context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl_context,
        ) as response:
            response_body = response.read().decode("utf-8")
            parsed_body: Any = json.loads(response_body) if response_body else None
            return {
                "status_code": response.status,
                "body": parsed_body,
            }
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ExecutorSubmitError(
            f"executor request failed with HTTP {exc.code}: {error_body}"
        ) from exc
    except URLError as exc:
        raise ExecutorSubmitError(f"executor request failed: {exc.reason}") from exc


def _get_json(
    url: str,
    *,
    timeout_seconds: float,
    ssl_context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    request = Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl_context,
        ) as response:
            response_body = response.read().decode("utf-8")
            parsed_body: Any = json.loads(response_body) if response_body else None
            if not isinstance(parsed_body, dict):
                raise ExecutorSubmitError("executor GET response must be a JSON object")
            return {"status_code": response.status, "body": parsed_body}
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ExecutorSubmitError(
            f"executor request failed with HTTP {exc.code}: {error_body}"
        ) from exc
    except URLError as exc:
        raise ExecutorSubmitError(f"executor request failed: {exc.reason}") from exc


def _executor_ssl_context(settings: AgentSettings) -> ssl.SSLContext | None:
    if not settings.executor_base_url.lower().startswith("https://"):
        return None
    context = ssl.create_default_context()
    if not settings.executor_tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def get_execution(
    settings: AgentSettings,
    execution_id: str,
) -> dict[str, Any]:
    return _get_json(
        _format_execution_url(settings.executor_execution_url, execution_id),
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )


def get_execution_result(
    settings: AgentSettings,
    execution_id: str,
) -> dict[str, Any]:
    return _get_json(
        _format_execution_url(settings.executor_result_url, execution_id),
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )


def get_execution_notebook(
    settings: AgentSettings,
    execution_id: str,
    *,
    view: str = "FULL",
    start_index: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    if view not in {"SUMMARY", "FULL"}:
        raise ValueError("notebook view must be SUMMARY or FULL")
    if start_index < 0:
        raise ValueError("start_index must be at least 0")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    base_url = _format_execution_url(settings.executor_notebook_url, execution_id)
    query = urlencode(
        {"view": view, "start_index": start_index, "limit": limit}
    )
    return _get_json(
        f"{base_url}?{query}",
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )


def submit_execution_start(
    settings: AgentSettings,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = _post_json(
        settings.executor_executions_url,
        payload,
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )
    if isinstance(response.get("body"), dict):
        ExecutorSubmitResponse.model_validate(response["body"])
    return response


def submit_execution_continue(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = _post_json(
        _format_execution_url(settings.executor_operations_url, execution_id),
        payload,
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )
    if isinstance(response.get("body"), dict):
        ExecutorSubmitResponse.model_validate(response["body"])
    return response


def submit_execution_finish(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _post_json(
        _format_execution_url(settings.executor_finalize_url, execution_id),
        payload,
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )


def submit_execution_cancel(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _post_json(
        _format_execution_url(settings.executor_cancel_url, execution_id),
        payload,
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )


def submit_execution_artifact(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _post_json(
        _format_execution_url(settings.executor_artifacts_url, execution_id),
        payload,
        timeout_seconds=settings.executor_timeout_seconds,
        ssl_context=_executor_ssl_context(settings),
    )


__all__ = [
    "ExecutorSubmitError",
    "get_execution",
    "get_execution_result",
    "get_execution_notebook",
    "submit_execution_cancel",
    "submit_execution_artifact",
    "submit_execution_continue",
    "submit_execution_finish",
    "submit_execution_start",
]

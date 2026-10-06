"""HTTP client helpers for submitting execution requests to Executor."""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any
from urllib.parse import urlencode
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx

from dtest.infrastructure.executor.routes import ExecutorRoute
from dtest.settings.agent import AgentSettings
from dtest.contracts.executor_transport import ExecutorTransport
from dtest.contracts.executor import ExecutorSubmitResponse
from dtest.contracts.execution import ExecutionNeedsRecovery


class ExecutorSubmitError(RuntimeError):
    """A bounded, sanitized error with a known retry classification."""

    def __init__(self, message, *, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class ExecutorOutcomeUnknown(ExecutionNeedsRecovery):
    """POST may have been accepted; preserve ownership instead of blind retry."""


@dataclass
class SubmissionEffects:
    possible_submissions: set[object] = field(default_factory=set)

    @property
    def may_have_submitted(self) -> bool:
        return bool(self.possible_submissions)


_effects: ContextVar[SubmissionEffects | None] = ContextVar(
    "executor_submission_effects", default=None
)


def current_submission_effects() -> SubmissionEffects | None:
    """Borrow the active invocation's tracker at a nested execution boundary."""
    return _effects.get()


@contextmanager
def submission_scope(effects=None):
    """Shared by child graph tasks, isolated from other concurrent invocations.

    A successful POST followed by failure/cancellation before the invocation
    finishes is also uncertain, even when the HTTP call itself has returned.
    """
    effects = effects if effects is not None else SubmissionEffects()
    token = _effects.set(effects)
    try:
        yield effects
    except BaseException as exc:
        if effects.may_have_submitted and not isinstance(
            exc, ExecutorOutcomeUnknown
        ):
            raise ExecutorOutcomeUnknown(
                "Executor POST may be accepted; reconcile its "
                "idempotency key before "
                "retry"
            ) from exc
        raise
    finally:
        _effects.reset(token)


class ExecutorClient:
    """One native async HTTP connection pool per graph runtime, never per Run."""

    def __init__(self, settings: AgentSettings, *, transport=None):
        self.settings = settings
        self.transport = transport
        self.http = None

    async def __aenter__(self):
        if self.http is not None:
            raise RuntimeError("ExecutorClient is already open")
        self.http = httpx.AsyncClient(
            transport=self.transport,
            verify=ssl.create_default_context()
            if self.settings.executor_tls_verify
            else False,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=self.settings.executor_http_max_connections,
                max_keepalive_connections=self.settings.executor_http_max_connections,
            ),
            timeout=httpx.Timeout(
                self.settings.executor_timeout_seconds,
                connect=self.settings.executor_http_connect_timeout_seconds,
                pool=self.settings.executor_http_pool_timeout_seconds,
            ),
        )
        return self

    async def __aexit__(self, *_):
        if self.http is not None:
            from dtest.lifecycle import protected_cleanup

            await protected_cleanup(self.http.aclose())

    async def request(self, method, url, payload=None):
        if self.http is None or self.http.is_closed:
            raise RuntimeError("ExecutorClient is not open")
        mutation = method == "POST"
        if mutation and (
            not isinstance(payload, dict) or not payload.get("idempotency_key")
        ):
            raise ExecutorSubmitError(
                "Executor POST requires idempotency_key", retryable=False
            )
        # Encode before marking possible delivery. Encoding errors send nothing.
        content = (
            json.dumps(payload, ensure_ascii=False, default=str).encode(
                "utf-8"
            )
            if mutation
            else None
        )
        effects = _effects.get()
        no_delivery = False
        attempt = object()
        if mutation and effects is not None:
            effects.possible_submissions.add(attempt)

        def not_delivered():
            nonlocal no_delivery
            no_delivery = True
            if mutation and effects is not None:
                effects.possible_submissions.discard(attempt)

        try:
            # End-to-end deadline also covers streaming trickle responses.
            async with asyncio.timeout(self.settings.executor_timeout_seconds):
                async with self.http.stream(
                    method,
                    url,
                    content=content,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    if response.status_code >= 300:
                        if mutation and (
                            response.status_code >= 500
                            or response.status_code in {408, 409}
                            or response.status_code < 400
                        ):
                            raise ExecutorOutcomeUnknown(
                                "Executor POST returned an "
                                "uncertain server "
                                "response"
                            )
                        not_delivered()
                        raise ExecutorSubmitError(
                            f"Executor HTTP {response.status_code}",
                            retryable=response.status_code == 429
                            or response.status_code >= 500,
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if (
                            len(body)
                            > self.settings.executor_http_max_response_bytes
                        ):
                            raise ExecutorSubmitError(
                                (
                                    "Executor response exceeds "
                                    "configured byte "
                                    "limit"
                                ),
                                retryable=False,
                            )
                    parsed = json.loads(body) if body else None
                    if method == "GET" and not isinstance(parsed, dict):
                        raise ExecutorSubmitError(
                            "Executor GET response must be a JSON object",
                            retryable=False,
                        )
                    return {
                        "status_code": response.status_code,
                        "body": parsed,
                    }
        except (httpx.InvalidURL, httpx.UnsupportedProtocol):
            not_delivered()
            raise ExecutorSubmitError(
                "Invalid Executor URL configuration", retryable=False
            ) from None
        except (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ConnectError):
            not_delivered()
            raise ExecutorSubmitError(
                "Executor connection unavailable before request delivery"
            ) from None
        except ExecutorOutcomeUnknown:
            raise
        except BaseException as exc:
            # Known HTTP rejection clears only this attempt; earlier accepted
            # mutations in the same graph invocation still require recovery.
            if mutation and not no_delivery:
                raise ExecutorOutcomeUnknown(
                    "Executor POST outcome unknown; reconcile its "
                    "idempotency "
                    "key"
                ) from exc
            if isinstance(exc, (asyncio.CancelledError, ExecutorSubmitError)):
                raise
            if isinstance(exc, (TimeoutError, httpx.HTTPError, ValueError)):
                raise ExecutorSubmitError(
                    "Executor GET failed or returned invalid JSON"
                ) from None
            raise


def _execution_url(
    base_url: str, route: ExecutorRoute, execution_id: str
) -> str:
    try:
        return route.url(base_url, execution_id)
    except ValueError as error:
        raise ExecutorSubmitError(str(error), retryable=False) from None


async def _post_json(url, payload, *, client):
    if client is None:
        raise RuntimeError("ExecutorClient must be owned by the graph runtime")
    return await client.request("POST", url, payload)


async def _get_json(url, *, client):
    if client is None:
        raise RuntimeError("ExecutorClient must be owned by the graph runtime")
    return await client.request("GET", url)


async def get_execution(
    settings: AgentSettings,
    execution_id: str,
    *,
    client: ExecutorTransport | None = None,
) -> dict[str, Any]:
    return await _get_json(
        _execution_url(
            settings.executor_base_url, ExecutorRoute.EXECUTION, execution_id
        ),
        client=client,
    )


async def get_execution_result(
    settings: AgentSettings,
    execution_id: str,
    *,
    client: ExecutorTransport | None = None,
) -> dict[str, Any]:
    return await _get_json(
        _execution_url(
            settings.executor_base_url, ExecutorRoute.RESULT, execution_id
        ),
        client=client,
    )


async def get_execution_notebook(
    settings: AgentSettings,
    execution_id: str,
    *,
    client: ExecutorTransport | None = None,
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
    base_url = _execution_url(
        settings.executor_base_url, ExecutorRoute.NOTEBOOK, execution_id
    )
    query = urlencode(
        {"view": view, "start_index": start_index, "limit": limit}
    )
    return await _get_json(
        f"{base_url}?{query}",
        client=client,
    )


async def submit_execution_start(
    settings: AgentSettings,
    payload: dict[str, Any],
    *,
    client: ExecutorTransport | None = None,
) -> dict[str, Any]:
    response = await _post_json(
        ExecutorRoute.EXECUTIONS.url(settings.executor_base_url),
        payload,
        client=client,
    )
    if not isinstance(response.get("body"), dict):
        raise ExecutorOutcomeUnknown(
            "Executor accepted POST but its receipt is missing"
        )
    if isinstance(response.get("body"), dict):
        try:
            ExecutorSubmitResponse.model_validate(response["body"])
        except ValueError:
            raise ExecutorOutcomeUnknown(
                "Executor accepted POST but its receipt is invalid"
            ) from None
    return response


async def submit_execution_continue(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
    *,
    client: ExecutorTransport | None = None,
) -> dict[str, Any]:
    response = await _post_json(
        _execution_url(
            settings.executor_base_url, ExecutorRoute.OPERATIONS, execution_id
        ),
        payload,
        client=client,
    )
    if not isinstance(response.get("body"), dict):
        raise ExecutorOutcomeUnknown(
            "Executor accepted POST but its receipt is missing"
        )
    if isinstance(response.get("body"), dict):
        try:
            ExecutorSubmitResponse.model_validate(response["body"])
        except ValueError:
            raise ExecutorOutcomeUnknown(
                "Executor accepted POST but its receipt is invalid"
            ) from None
    return response


async def submit_execution_finish(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
    *,
    client: ExecutorTransport | None = None,
) -> dict[str, Any]:
    return await _post_json(
        _execution_url(
            settings.executor_base_url, ExecutorRoute.FINALIZE, execution_id
        ),
        payload,
        client=client,
    )


async def submit_execution_cancel(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
    *,
    client: ExecutorTransport | None = None,
) -> dict[str, Any]:
    return await _post_json(
        _execution_url(
            settings.executor_base_url, ExecutorRoute.CANCEL, execution_id
        ),
        payload,
        client=client,
    )


async def submit_execution_artifact(
    settings: AgentSettings,
    execution_id: str,
    payload: dict[str, Any],
    *,
    client: ExecutorTransport | None = None,
) -> dict[str, Any]:
    return await _post_json(
        _execution_url(
            settings.executor_base_url, ExecutorRoute.ARTIFACTS, execution_id
        ),
        payload,
        client=client,
    )


__all__ = [
    "ExecutorClient",
    "ExecutorOutcomeUnknown",
    "submission_scope",
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

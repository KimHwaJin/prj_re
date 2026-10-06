"""Optional Phoenix/OpenTelemetry initialization."""

from __future__ import annotations

from typing import Any

from dtest.settings.agent import AgentSettings

from phoenix.otel import register


_tracer_provider: Any | None = None


def setup_phoenix(settings: AgentSettings) -> Any | None:
    """Register Phoenix once when a collector endpoint is configured."""
    global _tracer_provider

    if not settings.active_trace or not settings.phoenix_endpoint:
        return None
    if _tracer_provider is not None:
        return _tracer_provider

    headers = (
        {"Authorization": f"Bearer {settings.phoenix_api_key}"}
        if settings.phoenix_api_key
        else None
    )
    _tracer_provider = register(
        endpoint=settings.phoenix_endpoint,
        project_name=settings.phoenix_project_name,
        headers=headers,
        auto_instrument=True,
        batch=True,
    )
    return _tracer_provider


def shutdown_phoenix() -> None:
    """Flush and close the registered provider, if any."""
    global _tracer_provider

    provider = _tracer_provider
    _tracer_provider = None
    if provider is None:
        return
    force_flush = getattr(provider, "force_flush", None)
    if callable(force_flush):
        force_flush()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()

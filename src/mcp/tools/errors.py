"""Error event access tools — thin dispatcher over the active backend.

Implementations live in ``src/mcp/tools/_backends/{mock,otel}.py``.
With ``OBS_BACKEND=otel`` errors are synthesized from Jaeger spans
with ``error=true`` / ``otel.status_code=ERROR`` tags (the OTel demo
has no Sentry-equivalent service).
"""
from __future__ import annotations

from src.mcp.tools._backends import backend


async def get_errors(service: str, limit: int = 50) -> list[dict]:
    """Get error events for a service. Returns Sentry-style exceptions with stack traces."""
    return await backend().get_errors(service, limit)


async def get_error_by_trace(trace_id: str, limit: int = 50) -> list[dict]:
    """Get error events linked to a specific trace ID."""
    return await backend().get_error_by_trace(trace_id, limit)


async def get_error_by_correlation(correlation_id: str, limit: int = 100) -> list[dict]:
    """Get all error events linked to a correlation/incident ID."""
    return await backend().get_error_by_correlation(correlation_id, limit)

"""Log access tools — thin dispatcher over the active backend.

Implementations live in ``src/mcp/tools/_backends/{mock,otel}.py``.
Set ``OBS_BACKEND=otel`` to query Loki instead of the mock fixtures.
"""
from __future__ import annotations

from src.mcp.tools._backends import backend


async def search_logs(
    service: str,
    level: str = "",
    time_range: str = "1h",
    limit: int = 100,
) -> list[dict]:
    """Search log entries by service, optionally filter by level (INFO/WARN/ERROR/CRITICAL)."""
    return await backend().search_logs(service, level, time_range, limit)


async def get_error_logs(service: str, time_range: str = "1h", limit: int = 100) -> list[dict]:
    """Get ERROR and CRITICAL level logs for a service."""
    return await backend().get_error_logs(service, time_range, limit)


async def get_log_context(log_id: str, window: int = 3) -> list[dict]:
    """Get surrounding log lines around a specific log entry for temporal context."""
    return await backend().get_log_context(log_id, window)


async def get_logs_by_trace(trace_id: str, limit: int = 100) -> list[dict]:
    """Get log entries linked to a specific trace ID."""
    return await backend().get_logs_by_trace(trace_id, limit)


async def get_logs_by_correlation(correlation_id: str, limit: int = 200) -> list[dict]:
    """Get log entries linked to a correlation/incident ID."""
    return await backend().get_logs_by_correlation(correlation_id, limit)

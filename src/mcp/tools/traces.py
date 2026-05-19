"""Trace access tools — thin dispatcher over the active backend.

Implementations live in ``src/mcp/tools/_backends/{mock,otel}.py``.
Set ``OBS_BACKEND=otel`` to query Jaeger instead of the mock fixtures.
"""
from __future__ import annotations

from src.mcp.tools._backends import backend


async def search_traces(service: str, time_range: str = "1h", limit: int = 20) -> list[dict]:
    """Search distributed traces by service name. Returns traces with span details."""
    return await backend().search_traces(service, time_range, limit)


async def get_trace(trace_id: str) -> dict:
    """Get a single trace by ID with full span tree."""
    return await backend().get_trace(trace_id)


async def get_slow_spans(service: str, threshold_ms: int = 1000, limit: int = 50) -> list[dict]:
    """Find spans slower than a threshold for a given service."""
    return await backend().get_slow_spans(service, threshold_ms, limit)


async def get_traces_by_correlation(correlation_id: str, limit: int = 50) -> list[dict]:
    """Get all traces linked to a correlation/incident ID."""
    return await backend().get_traces_by_correlation(correlation_id, limit)

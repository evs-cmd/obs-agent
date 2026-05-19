"""Correlation tool — thin dispatcher over the active backend.

Pre-joins logs/metrics/traces/errors by trace_id + correlation_id.
Implementations live in ``src/mcp/tools/_backends/{mock,otel}.py``.

With ``OBS_BACKEND=otel`` the join is anchored on a recent error trace
(the demo doesn't propagate correlation_id); production deployments
that propagate it via baggage will get a sharper join automatically.
"""
from __future__ import annotations

from src.mcp.tools._backends import backend


async def get_incident_context(
    service: str,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    log_limit: int = 100,
    trace_limit: int = 20,
    error_limit: int = 50,
    metric_limit: int = 100,
) -> dict:
    """Get pre-correlated incident context by joining logs, metrics, traces, and errors.

    Uses trace_id as primary join key. Provide service name; optionally narrow with
    trace_id or correlation_id. Returns ONLY raw correlated data — analysis happens
    in the retrieval layer.
    """
    return await backend().get_incident_context(
        service=service,
        trace_id=trace_id,
        correlation_id=correlation_id,
        log_limit=log_limit,
        trace_limit=trace_limit,
        error_limit=error_limit,
        metric_limit=metric_limit,
    )

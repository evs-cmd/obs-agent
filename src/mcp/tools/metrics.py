"""Metric access tools — thin dispatcher over the active backend.

Implementations live in ``src/mcp/tools/_backends/{mock,otel}.py``.
Set ``OBS_BACKEND=otel`` to query Prometheus instead of the mock fixtures.

Analysis (detect_anomalies, summarize_metrics, p99) lives in
src/retrieval/metrics.py — backend-agnostic.
"""
from __future__ import annotations

from src.mcp.tools._backends import backend


async def get_metrics(service: str, limit: int = 100) -> list[dict]:
    """Get all metric datapoints for a service. Returns time-series rows."""
    return await backend().get_metrics(service, limit)


async def get_metric(name: str, service: str, limit: int = 100) -> list[dict]:
    """Get a specific metric time-series for a service.

    Metric names: error_rate, p99_latency_ms, db_pool_active, db_pool_max,
    circuit_breaker_state, success_rate, throughput_rpm.
    """
    return await backend().get_metric(name, service, limit)

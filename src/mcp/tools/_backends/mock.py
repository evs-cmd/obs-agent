"""Mock backend — reads data/*.json fixtures.

This module is the *implementation* behind the tool files when
``OBS_BACKEND=mock`` (the default). The tool files in
``src/mcp/tools/{logs,metrics,traces,errors,correlation}.py`` dispatch
here via the ``backend()`` selector.

Shape is the contract: every function returns the same Python shape it
returned before the backend split — agents, retrieval summarizers, and
tests all depend on it.
"""
from __future__ import annotations

import asyncio

from src.mcp.tools._data import load_json


# ─── Logs ────────────────────────────────────────────────────────────────

async def search_logs(
    service: str,
    level: str = "",
    time_range: str = "1h",
    limit: int = 100,
) -> list[dict]:
    data = await load_json("logs.json")
    results = [l for l in data["logs"] if l["service"] == service]
    if level:
        results = [l for l in results if l["level"] == level.upper()]
    return results[:limit]


async def get_error_logs(service: str, time_range: str = "1h", limit: int = 100) -> list[dict]:
    data = await load_json("logs.json")
    results = [l for l in data["logs"] if l["service"] == service and l["level"] in ("ERROR", "CRITICAL")]
    return results[:limit]


async def get_log_context(log_id: str, window: int = 3) -> list[dict]:
    data = await load_json("logs.json")
    logs = data["logs"]
    for i, log in enumerate(logs):
        if log["id"] == log_id:
            start = max(0, i - window)
            end = min(len(logs), i + window + 1)
            return logs[start:end]
    return [{"error": f"Log {log_id} not found"}]


async def get_logs_by_trace(trace_id: str, limit: int = 100) -> list[dict]:
    data = await load_json("logs.json")
    results = [l for l in data["logs"] if l.get("trace_id") == trace_id]
    return results[:limit]


async def get_logs_by_correlation(correlation_id: str, limit: int = 200) -> list[dict]:
    data = await load_json("logs.json")
    results = [l for l in data["logs"] if l.get("correlation_id") == correlation_id]
    return results[:limit]


# ─── Metrics ─────────────────────────────────────────────────────────────

async def get_metrics(service: str, limit: int = 100) -> list[dict]:
    data = await load_json("metrics.json")
    results = [m for m in data["metrics"] if m["service"] == service]
    return results[:limit]


async def get_metric(name: str, service: str, limit: int = 100) -> list[dict]:
    data = await load_json("metrics.json")
    results = [m for m in data["metrics"] if m["service"] == service and m["metric"] == name]
    return results[:limit]


# ─── Traces ──────────────────────────────────────────────────────────────

async def search_traces(service: str, time_range: str = "1h", limit: int = 20) -> list[dict]:
    data = await load_json("traces.json")
    results = [t for t in data["traces"] if t["service"] == service]
    return results[:limit]


async def get_trace(trace_id: str) -> dict:
    data = await load_json("traces.json")
    for t in data["traces"]:
        if t["trace_id"] == trace_id:
            return t
    return {"error": f"Trace {trace_id} not found"}


async def get_slow_spans(service: str, threshold_ms: int = 1000, limit: int = 50) -> list[dict]:
    data = await load_json("traces.json")
    slow = []
    for trace in data["traces"]:
        for sp in trace["spans"]:
            if sp.get("service") == service and sp["duration_ms"] > threshold_ms:
                slow.append({**sp, "trace_id": trace["trace_id"]})
    return slow[:limit]


async def get_traces_by_correlation(correlation_id: str, limit: int = 50) -> list[dict]:
    data = await load_json("traces.json")
    results = [t for t in data["traces"] if t.get("correlation_id") == correlation_id]
    return results[:limit]


# ─── Errors ──────────────────────────────────────────────────────────────

async def get_errors(service: str, limit: int = 50) -> list[dict]:
    data = await load_json("errors.json")
    results = [e for e in data["errors"] if e["service"] == service]
    return results[:limit]


async def get_error_by_trace(trace_id: str, limit: int = 50) -> list[dict]:
    data = await load_json("errors.json")
    results = [e for e in data["errors"] if e.get("trace_id") == trace_id]
    return results[:limit]


async def get_error_by_correlation(correlation_id: str, limit: int = 100) -> list[dict]:
    data = await load_json("errors.json")
    results = [e for e in data["errors"] if e.get("correlation_id") == correlation_id]
    return results[:limit]


# ─── Correlation (the join) ──────────────────────────────────────────────

def _extract_services(traces: list[dict]) -> list[str]:
    services: set[str] = set()
    for t in traces:
        for sp in t.get("spans", []):
            if svc := sp.get("service"):
                services.add(svc)
    return sorted(services)


def _find_root_service(traces: list[dict]) -> str | None:
    for t in traces:
        for sp in t.get("spans", []):
            if not sp.get("parent"):
                return sp.get("service")
    return None


def _find_error_traces(traces: list[dict], service: str) -> list[dict]:
    return [
        t for t in traces
        if t["status"] == "error"
        and any(s.get("service") == service for s in t.get("spans", []))
    ]


async def get_incident_context(
    service: str,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    log_limit: int = 100,
    trace_limit: int = 20,
    error_limit: int = 50,
    metric_limit: int = 100,
) -> dict:
    traces_data, logs_data, errors_data, metrics_data = await asyncio.gather(
        load_json("traces.json"),
        load_json("logs.json"),
        load_json("errors.json"),
        load_json("metrics.json"),
    )

    traces = traces_data["traces"]
    logs = logs_data["logs"]
    errors = errors_data["errors"]
    metrics = metrics_data["metrics"]

    if not trace_id:
        error_traces = _find_error_traces(traces, service)
        if error_traces:
            trace_id = error_traces[0]["trace_id"]
            if not correlation_id:
                correlation_id = error_traces[0].get("correlation_id")

    if trace_id and not correlation_id:
        for t in traces:
            if t["trace_id"] == trace_id:
                correlation_id = t.get("correlation_id")
                break

    if correlation_id:
        related_traces = [t for t in traces if t.get("correlation_id") == correlation_id][:trace_limit]
        related_logs = [l for l in logs if l.get("correlation_id") == correlation_id][:log_limit]
        related_errors = [e for e in errors if e.get("correlation_id") == correlation_id][:error_limit]
    else:
        related_traces = [t for t in traces if any(s.get("service") == service for s in t.get("spans", []))][:trace_limit]
        related_logs = [l for l in logs if l.get("service") == service][:log_limit]
        related_errors = [e for e in errors if e.get("service") == service][:error_limit]

    involved_services = _extract_services(related_traces)
    root_service = _find_root_service(related_traces)

    related_metrics: dict[str, list[dict]] = {}
    for svc in involved_services:
        svc_metrics = [m for m in metrics if m["service"] == svc][:metric_limit]
        if svc_metrics:
            related_metrics[svc] = svc_metrics

    return {
        "trace_id": trace_id,
        "correlation_id": correlation_id,
        "services": involved_services,
        "root_service": root_service,
        "traces": related_traces,
        "logs": related_logs,
        "errors": related_errors,
        "metrics": related_metrics,
    }

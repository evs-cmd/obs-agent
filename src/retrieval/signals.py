"""Signal compression — pure functions over raw MCP outputs.

Reduces raw log/metric/trace data to high-signal summaries before the LLM
sees it. Inputs are validated against `src/mcp/tools/schemas.py` so MCP
schema drift fails loudly here instead of producing silent zeros downstream.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.mcp.tools.schemas import LogEntry, MetricPoint, Trace

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _coerce(rows: list[dict], model: type[T]) -> list[T]:
    """Validate raw dicts against `model`. Bad records are logged and skipped."""
    out: list[T] = []
    for r in rows:
        try:
            out.append(model.model_validate(r))
        except ValidationError as e:
            logger.warning("signals: dropping invalid %s record: %s", model.__name__, e)
    return out


# ── logs ─────────────────────────────────────────────────────────────────

_SEVERITY = {"CRITICAL": 4, "ERROR": 3, "WARN": 2, "INFO": 1, "DEBUG": 0}
_LEVEL_ALIASES = {"FATAL": "CRITICAL", "WARNING": "WARN", "ERR": "ERROR"}


def _normalize_level(raw: str) -> str:
    up = (raw or "INFO").upper()
    return _LEVEL_ALIASES.get(up, up)


def summarize_logs(logs: list[dict], top_k: int = 5) -> dict[str, Any]:
    """Cluster logs by message, rank by severity then frequency, top-K with samples."""
    entries = _coerce(logs, LogEntry)
    if not entries:
        return {"total_logs": 0, "unique_messages": 0, "top_clusters": [], "level_distribution": {}}
    clusters: dict[str, list[LogEntry]] = {}
    for log in entries:
        clusters.setdefault(log.message, []).append(log)
    ranked = sorted(
        clusters.items(),
        key=lambda kv: (_SEVERITY.get(_normalize_level(kv[1][0].level), 0), len(kv[1])),
        reverse=True,
    )
    top = [{
        "message": msg, "count": len(g),
        "level": _normalize_level(g[0].level),
        "service": g[0].service, "sample_id": g[0].id,
        "first_seen": min(x.timestamp for x in g),
        "last_seen":  max(x.timestamp for x in g),
    } for msg, g in ranked[:top_k]]
    return {
        "total_logs": len(entries),
        "unique_messages": len(clusters),
        "top_clusters": top,
        "level_distribution": dict(Counter(_normalize_level(l.level) for l in entries)),
    }


# ── metrics ──────────────────────────────────────────────────────────────

def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    return values[max(0, min(len(values) - 1, int(p * len(values)) - 1))]


def summarize_metrics(metrics: list[dict], anomaly_threshold: float = 3.0) -> dict[str, Any]:
    """Per-metric stats + anomaly flags in one pass.

    Returns `{"summary": {metric_name: stats}, "anomalies": [...]}`. An anomaly
    is a metric whose last/first ratio exceeds `anomaly_threshold` in either direction.
    """
    points = _coerce(metrics, MetricPoint)
    if not points:
        return {"summary": {}, "anomalies": []}
    by_name: dict[str, list[MetricPoint]] = defaultdict(list)
    for m in points:
        by_name[m.metric].append(m)
    summary: dict[str, Any] = {}
    anomalies: list[dict] = []
    for name, rows in by_name.items():
        rows_sorted = sorted(rows, key=lambda r: r.timestamp)
        values = sorted(r.value for r in rows_sorted)
        first, last = rows_sorted[0].value, rows_sorted[-1].value
        ratio = round(last / first, 2) if first else None
        summary[name] = {
            "count": len(values),
            "p50": _pct(values, 0.50),
            "p95": _pct(values, 0.95),
            "p99": _pct(values, 0.99),
            "min": values[0], "max": values[-1],
            "first_value": first, "last_value": last,
            "change_ratio": ratio,
            "unit": rows_sorted[0].unit,
        }
        if ratio is not None and len(rows) >= 2 and (
            ratio > anomaly_threshold or 0 < ratio < 1 / anomaly_threshold
        ):
            anomalies.append({
                "metric": name, "service": rows_sorted[0].service,
                "first_value": first, "last_value": last,
                "change_ratio": ratio,
                "direction": "spike" if ratio > 1 else "drop",
                "unit": rows_sorted[0].unit,
            })
    return {"summary": summary, "anomalies": anomalies}


# ── traces ───────────────────────────────────────────────────────────────

def build_trace_skeleton(trace: dict, slowest_k: int = 3) -> dict[str, Any]:
    """Keep only the high-signal spans of a trace: root + errors + slowest-K."""
    try:
        t = Trace.model_validate(trace)
    except ValidationError as e:
        logger.warning("signals: invalid trace: %s", e)
        return {
            "trace_id": trace.get("trace_id"),
            "duration_ms": trace.get("duration_ms"),
            "status": trace.get("status"),
            "spans": [],
        }
    if not t.spans:
        return {"trace_id": t.trace_id, "duration_ms": t.duration_ms,
                "status": t.status, "spans": []}
    root = next((s for s in t.spans if not s.parent), t.spans[0])
    errors = [s for s in t.spans if s.status == "error"]
    slowest = sorted(t.spans, key=lambda s: s.duration_ms, reverse=True)[:slowest_k]
    keep = {s.span_id for s in [root, *errors, *slowest]}
    return {
        "trace_id": t.trace_id,
        "duration_ms": t.duration_ms,
        "status": t.status,
        "root_service": root.service,
        "root_operation": root.operation,
        "error_count": len(errors),
        "total_spans": len(t.spans),
        "spans": [s.model_dump() for s in t.spans if s.span_id in keep],
    }


def summarize_traces(traces: list[dict], top_k: int = 5) -> dict[str, Any]:
    """Skeletons for top-K traces by duration, plus overall stats."""
    if not traces:
        return {"total_traces": 0, "error_traces": 0, "skeletons": []}
    by_duration = sorted(traces, key=lambda t: t.get("duration_ms", 0), reverse=True)
    return {
        "total_traces": len(traces),
        "error_traces": sum(1 for t in traces if t.get("status") == "error"),
        "max_duration_ms": max(t.get("duration_ms", 0) for t in traces),
        "skeletons": [build_trace_skeleton(t) for t in by_duration[:top_k]],
    }


# ── dependency graph ────────────────────────────────────────────────────
#
# Walks parent/child links across traces and emits a service-call graph.
# Saves the LLM the inference step of "which service depends on which" —
# given a cascade failure, it can read direction straight from this map.

def build_dependency_graph(traces: list[dict]) -> dict[str, list[str]]:
    """Return ``{service: sorted_downstream_services}`` aggregated over traces.

    Edge direction is caller → callee (parent span's service → child span's
    service). Self-loops (same service calling itself) are dropped. Missing
    fields are tolerated — bad spans just don't contribute edges.
    """
    edges: dict[str, set[str]] = defaultdict(set)
    for trace in traces:
        spans = trace.get("spans") or []
        # span_id → service lookup for this trace
        idx = {sp.get("span_id"): sp.get("service") for sp in spans if sp.get("span_id")}
        for sp in spans:
            parent_id = sp.get("parent")
            child_svc = sp.get("service")
            if not parent_id or not child_svc:
                continue
            parent_svc = idx.get(parent_id)
            if not parent_svc or parent_svc == child_svc:
                continue
            edges[parent_svc].add(child_svc)
    return {svc: sorted(downstream) for svc, downstream in sorted(edges.items())}

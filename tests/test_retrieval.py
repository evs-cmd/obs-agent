from src.retrieval.planner import plan_query
from src.retrieval.signals import (
    build_dependency_graph,
    build_trace_skeleton,
    summarize_logs,
    summarize_metrics,
    summarize_traces,
)


def test_summarize_logs_clusters_by_message():
    logs = [
        {"id": "1", "message": "DB timeout", "level": "ERROR", "service": "payment", "timestamp": "2026-05-13T10:00:00Z"},
        {"id": "2", "message": "DB timeout", "level": "ERROR", "service": "payment", "timestamp": "2026-05-13T10:01:00Z"},
        {"id": "3", "message": "connection refused", "level": "ERROR", "service": "payment", "timestamp": "2026-05-13T10:02:00Z"},
    ]
    summary = summarize_logs(logs)
    assert summary["total_logs"] == 3
    assert summary["unique_messages"] == 2
    assert summary["top_clusters"][0]["count"] == 2
    assert summary["top_clusters"][0]["message"] == "DB timeout"


def test_summarize_logs_empty():
    assert summarize_logs([])["total_logs"] == 0


def test_summarize_metrics_computes_percentiles():
    metrics = [
        {"timestamp": f"2026-05-13T10:0{i}:00Z", "service": "checkout", "metric": "p99_latency_ms", "value": v, "unit": "ms"}
        for i, v in enumerate([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    ]
    result = summarize_metrics(metrics)
    p99 = result["summary"]["p99_latency_ms"]
    assert p99["count"] == 10
    assert p99["min"] == 100
    assert p99["max"] == 1000


def test_summarize_metrics_flags_anomaly_spike():
    metrics = [
        {"timestamp": "2026-05-13T10:00:00Z", "service": "payment", "metric": "error_rate", "value": 0.01, "unit": "ratio"},
        {"timestamp": "2026-05-13T10:05:00Z", "service": "payment", "metric": "error_rate", "value": 0.78, "unit": "ratio"},
    ]
    anomalies = summarize_metrics(metrics, anomaly_threshold=3.0)["anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["direction"] == "spike"
    assert anomalies[0]["change_ratio"] > 3


def test_build_trace_skeleton_keeps_errors_and_slowest():
    trace = {
        "trace_id": "t1",
        "duration_ms": 5000,
        "status": "error",
        "spans": [
            {"span_id": "s1", "service": "gateway", "operation": "POST /checkout", "duration_ms": 5000, "status": "error"},
            {"span_id": "s2", "service": "payment", "operation": "charge", "duration_ms": 4900, "parent": "s1", "status": "error", "error": "timeout"},
            {"span_id": "s3", "service": "inventory", "operation": "reserve", "duration_ms": 30, "parent": "s1", "status": "ok"},
        ],
    }
    skeleton = build_trace_skeleton(trace)
    assert skeleton["error_count"] == 2
    assert skeleton["root_service"] == "gateway"
    assert skeleton["total_spans"] == 3


def test_summarize_traces_picks_top_by_duration():
    traces = [
        {"trace_id": "t1", "duration_ms": 100, "status": "ok", "spans": []},
        {"trace_id": "t2", "duration_ms": 5000, "status": "error", "spans": []},
    ]
    out = summarize_traces(traces, top_k=1)
    assert out["total_traces"] == 2
    assert out["error_traces"] == 1
    assert out["skeletons"][0]["trace_id"] == "t2"


def test_plan_query_error_intent():
    plan = plan_query("show me payment errors")
    assert plan.intent == "error-focused"
    assert plan.log_limit > plan.metric_limit


def test_plan_query_latency_intent():
    plan = plan_query("why is checkout slow?")
    assert plan.intent == "latency-focused"
    assert plan.metric_limit > plan.log_limit


def test_plan_query_default():
    plan = plan_query("hello world")
    assert plan.intent == "default"


def test_build_dependency_graph_directional_edges():
    """Caller→callee edges aggregated across traces. Self-loops dropped, sorted."""
    traces = [
        {
            "trace_id": "t1",
            "spans": [
                {"span_id": "a", "service": "checkout"},
                {"span_id": "b", "service": "payment", "parent": "a"},
                {"span_id": "c", "service": "inventory", "parent": "a"},
                {"span_id": "d", "service": "payment", "parent": "b"},  # self-loop → dropped
            ],
        },
        {
            "trace_id": "t2",
            "spans": [
                {"span_id": "x", "service": "checkout"},
                {"span_id": "y", "service": "fraud", "parent": "x"},
            ],
        },
        # Bad/missing fields: tolerated, just don't contribute edges
        {"trace_id": "t3", "spans": [{"service": "nobody"}]},
    ]
    graph = build_dependency_graph(traces)
    assert graph == {"checkout": ["fraud", "inventory", "payment"]}


def test_build_dependency_graph_empty():
    assert build_dependency_graph([]) == {}
    assert build_dependency_graph([{"trace_id": "x", "spans": []}]) == {}


def test_summarize_logs_normalizes_level_aliases():
    """`FATAL`/`WARNING`/lowercase should map to canonical CRITICAL/WARN."""
    logs = [
        {"id": "1", "message": "boom", "level": "fatal",   "service": "x", "timestamp": "2026-05-13T10:00:00Z"},
        {"id": "2", "message": "slow", "level": "WARNING", "service": "x", "timestamp": "2026-05-13T10:01:00Z"},
        {"id": "3", "message": "ok",   "level": "info",    "service": "x", "timestamp": "2026-05-13T10:02:00Z"},
    ]
    summary = summarize_logs(logs)
    # CRITICAL (from FATAL) ranks above WARN above INFO
    assert summary["top_clusters"][0]["level"] == "CRITICAL"
    assert "CRITICAL" in summary["level_distribution"]
    assert "WARN" in summary["level_distribution"]

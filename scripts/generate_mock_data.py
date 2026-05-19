#!/usr/bin/env python3
"""Generate realistic mock observability data with multiple incident scenarios.

Each scenario produces correlated logs, traces, metrics, and errors that tell
a coherent story. Data is written to data/*.json, replacing existing files.

Usage: python scripts/generate_mock_data.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
random.seed(42)

SERVICES = [
    "checkout-service", "payment-service", "gateway-service",
    "inventory-service", "user-service", "notification-service",
    "search-service", "recommendation-service",
]

HOSTS = {svc: f"ip-10-0-{i}-{random.randint(10,99)}" for i, svc in enumerate(SERVICES)}
PODS = {svc: f"{svc.split('-')[0]}-{random.randbytes(3).hex()[:6]}" for svc in SERVICES}
RELEASES = {svc: f"{svc.split('-')[0]}-v{random.randint(1,3)}.{random.randint(0,20)}.{random.randint(0,9)}" for svc in SERVICES}

# ── Counters ────────────────────────────────────────────────────────────────

_id_counters: dict[str, int] = {}

def _next_id(prefix: str) -> str:
    _id_counters[prefix] = _id_counters.get(prefix, 0) + 1
    return f"{prefix}-{_id_counters[prefix]:04d}"


def ts(base: datetime, offset_sec: int = 0) -> str:
    return (base + timedelta(seconds=offset_sec)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Scenario builders ──────────────────────────────────────────────────────

all_logs: list[dict] = []
all_traces: list[dict] = []
all_metrics: list[dict] = []
all_errors: list[dict] = []


def _log(timestamp: str, service: str, level: str, message: str, *,
         trace_id: str | None = None, span_id: str | None = None,
         correlation_id: str | None = None, request_id: str | None = None,
         http_status: int | None = None, duration_ms: int | None = None) -> dict:
    entry = {
        "id": _next_id("log"),
        "timestamp": timestamp,
        "service": service,
        "level": level,
        "message": message,
        "trace_id": trace_id,
        "span_id": span_id,
        "correlation_id": correlation_id,
        "host": HOSTS.get(service, "infra-01"),
        "pod": PODS.get(service, service),
        "request_id": request_id,
        "http_status": http_status,
        "duration_ms": duration_ms,
    }
    all_logs.append(entry)
    return entry


def _trace(timestamp: str, service: str, duration_ms: int, status: str,
           spans: list[dict], *, correlation_id: str | None = None) -> dict:
    entry = {
        "trace_id": _next_id("t"),
        "correlation_id": correlation_id,
        "service": service,
        "duration_ms": duration_ms,
        "status": status,
        "timestamp": timestamp,
        "spans": spans,
    }
    all_traces.append(entry)
    return entry


def _error(timestamp: str, service: str, error_type: str, message: str,
           stack_trace: str, *, trace_id: str | None = None, span_id: str | None = None,
           correlation_id: str | None = None, count: int = 1) -> dict:
    entry = {
        "error_id": _next_id("err"),
        "timestamp": timestamp,
        "service": service,
        "type": error_type,
        "message": message,
        "stack_trace": stack_trace,
        "trace_id": trace_id,
        "span_id": span_id,
        "correlation_id": correlation_id,
        "env": "prod",
        "release": RELEASES[service],
        "count": count,
        "first_seen": timestamp,
        "last_seen": timestamp,
    }
    all_errors.append(entry)
    return entry


def _metric(timestamp: str, service: str, metric: str, value: float, unit: str) -> dict:
    entry = {
        "timestamp": timestamp,
        "service": service,
        "metric": metric,
        "value": round(value, 3),
        "unit": unit,
    }
    all_metrics.append(entry)
    return entry


def _span(service: str, operation: str, duration_ms: int, status: str = "ok",
          parent: str | None = None, http_status: int = 200, error: str | None = None) -> dict:
    s = {
        "span_id": _next_id("s"),
        "service": service,
        "operation": operation,
        "duration_ms": duration_ms,
        "status": status,
        "http_status": http_status,
    }
    if parent:
        s["parent"] = parent
    if error:
        s["error"] = error
    return s


# ── Scenario 1: Stripe payment gateway outage ──────────────────────────────
# Stripe goes down → payment-service timeouts → checkout-service 504s →
# circuit breaker opens → DB pool exhaustion from retries

def scenario_stripe_outage():
    corr = "inc-2026-0513-001"
    base = datetime(2026, 5, 13, 10, 20, 0, tzinfo=timezone.utc)

    # Healthy baseline
    for i in range(3):
        t = ts(base, i * 5)
        for svc in ["payment-service", "checkout-service", "gateway-service"]:
            _metric(t, svc, "error_rate", random.uniform(0.001, 0.01), "ratio")
            _metric(t, svc, "p99_latency_ms", random.uniform(50, 200), "ms")
            _metric(t, svc, "success_rate", random.uniform(0.99, 1.0), "ratio")
            _metric(t, svc, "throughput_rpm", random.uniform(800, 1200), "rpm")

    _metric(ts(base, 0), "payment-service", "db_pool_active", 12, "connections")
    _metric(ts(base, 0), "payment-service", "db_pool_max", 50, "connections")
    _metric(ts(base, 0), "payment-service", "circuit_breaker_state", 0, "state")

    # Healthy traces
    root_span = _span("checkout-service", "POST /checkout", 320)
    _trace(ts(base, 15), "checkout-service", 320, "ok", [
        root_span,
        _span("payment-service", "POST /charge", 180, parent=root_span["span_id"]),
        _span("inventory-service", "POST /reserve", 45, parent=root_span["span_id"]),
    ])

    _log(ts(base, 0), "payment-service", "INFO", "Service started, DB pool initialized: 50 max connections")
    _log(ts(base, 15), "checkout-service", "INFO", "Checkout completed successfully",
         request_id="req-ok-001", http_status=200, duration_ms=320)

    # ── Incident begins: stripe goes unresponsive ──
    incident_start = 120  # 2 minutes in

    _log(ts(base, incident_start), "gateway-service", "WARN",
         "Elevated latency to stripe-gateway: p99=2400ms (threshold=500ms)", correlation_id=corr)

    for i, offset in enumerate(range(incident_start + 10, incident_start + 180, 15)):
        req_id = f"req-c{i+10:03d}"
        tid = _next_id("t")

        # Error traces
        root_s = _span("checkout-service", "POST /checkout", 3000 + i * 200, "error", http_status=504)
        pay_s = _span("payment-service", "POST /charge", 3000 + i * 150, "error",
                       parent=root_s["span_id"], http_status=504,
                       error="ConnectionTimeout: stripe-gateway unreachable")
        _trace(ts(base, offset), "checkout-service", 3000 + i * 200, "error", [
            root_s, pay_s,
            _span("inventory-service", "POST /reserve", 45, parent=root_s["span_id"]),
        ], correlation_id=corr)

        # Connection timeout logs
        _log(ts(base, offset), "payment-service", "ERROR",
             f"ConnectionTimeout: failed to reach stripe-gateway after 3 retries",
             trace_id=tid, correlation_id=corr, request_id=req_id,
             http_status=504, duration_ms=3000 + i * 150)

        _error(ts(base, offset), "payment-service", "ConnectionTimeout",
               "Failed to reach stripe-gateway after 3 retries",
               "payment.gateway.StripeClient.charge() → httpx.TimeoutException → payment.retry.RetryHandler.execute()",
               trace_id=tid, span_id=pay_s["span_id"], correlation_id=corr)

        # DB pool exhaustion from retry storms
        pool_active = min(50, 25 + i * 3)
        waiting = max(0, pool_active - 47) * 3
        if pool_active >= 48:
            _log(ts(base, offset + 2), "payment-service", "ERROR",
                 f"DB connection pool exhausted: {pool_active}/50 connections in use, {waiting} requests waiting",
                 correlation_id=corr)

        # Degrading metrics
        _metric(ts(base, offset), "payment-service", "error_rate", min(0.85, 0.05 + i * 0.07), "ratio")
        _metric(ts(base, offset), "payment-service", "p99_latency_ms", 3000 + i * 200, "ms")
        _metric(ts(base, offset), "payment-service", "db_pool_active", pool_active, "connections")
        _metric(ts(base, offset), "payment-service", "success_rate", max(0.15, 0.95 - i * 0.07), "ratio")
        _metric(ts(base, offset), "checkout-service", "error_rate", min(0.8, 0.03 + i * 0.06), "ratio")
        _metric(ts(base, offset), "checkout-service", "p99_latency_ms", 3200 + i * 250, "ms")

    # Circuit breaker opens
    cb_open_offset = incident_start + 90
    _log(ts(base, cb_open_offset), "payment-service", "WARN",
         "Circuit breaker OPEN for stripe-gateway: failure threshold exceeded (10/10 failures in 60s)",
         correlation_id=corr)
    _metric(ts(base, cb_open_offset), "payment-service", "circuit_breaker_state", 1, "state")

    for i in range(4):
        offset = cb_open_offset + 15 + i * 20
        req_id = f"req-cb{i+1:03d}"
        _log(ts(base, offset), "payment-service", "ERROR",
             f"Circuit breaker OPEN: rejecting charge request for order ord-{9990+i} without attempting upstream",
             correlation_id=corr, request_id=req_id, http_status=503)

    # Probe attempts
    for i, offset in enumerate([cb_open_offset + 60, cb_open_offset + 120]):
        _log(ts(base, offset), "payment-service", "WARN",
             f"Probe failed: stripe-gateway {'still unreachable' if i == 0 else 'returned 503'}, circuit remains OPEN",
             correlation_id=corr, http_status=504 if i == 0 else 503,
             request_id=f"req-probe-{i+1:03d}", duration_ms=5000 + i * 100)

    # Gateway-level errors
    for offset in [incident_start + 60, incident_start + 120]:
        _log(ts(base, offset), "gateway-service", "ERROR",
             f"GatewayTimeout: Stripe API unresponsive, request_id=req-{random.randint(7000,9000)}",
             correlation_id=corr, http_status=504, duration_ms=5000 + random.randint(0, 500))

    # Alertmanager
    _log(ts(base, incident_start + 30), "alertmanager", "CRITICAL",
         "[FIRING] PaymentServiceErrorRate > 50% for 2m | checkout-service affected",
         correlation_id=corr)


# ── Scenario 2: Kafka consumer lag → search degradation ────────────────────
# search-service Kafka consumer falls behind → stale search results →
# recommendation-service returns empty → user-service sees elevated errors

def scenario_kafka_lag():
    corr = "inc-2026-0513-002"
    base = datetime(2026, 5, 13, 14, 0, 0, tzinfo=timezone.utc)

    # Healthy baseline
    for svc in ["search-service", "recommendation-service", "user-service"]:
        _metric(ts(base, 0), svc, "error_rate", 0.005, "ratio")
        _metric(ts(base, 0), svc, "p99_latency_ms", random.uniform(30, 100), "ms")
        _metric(ts(base, 0), svc, "throughput_rpm", random.uniform(500, 1500), "rpm")

    _log(ts(base, 0), "search-service", "INFO", "Kafka consumer group healthy, lag=0 across 12 partitions")

    # Lag builds up
    incident_start = 60
    for i, offset in enumerate(range(incident_start, incident_start + 300, 30)):
        lag = 500 * (i + 1) ** 2
        _log(ts(base, offset), "search-service", "WARN",
             f"Kafka consumer lag rising: {lag} messages behind on topic product-updates (partitions 0-11)",
             correlation_id=corr)
        _metric(ts(base, offset), "search-service", "kafka_consumer_lag", lag, "messages")
        _metric(ts(base, offset), "search-service", "p99_latency_ms", 100 + i * 400, "ms")

        if i >= 2:
            _log(ts(base, offset + 5), "search-service", "ERROR",
                 f"Search index {lag // 1000}k messages behind. Results may be stale.",
                 correlation_id=corr)

            root_s = _span("user-service", "GET /search", 800 + i * 300, "error" if i > 4 else "ok",
                           http_status=500 if i > 4 else 200)
            search_s = _span("search-service", "POST /query", 600 + i * 250,
                             "error" if i > 3 else "ok", parent=root_s["span_id"],
                             http_status=504 if i > 3 else 200,
                             error="SearchTimeout: index replication lag" if i > 3 else None)
            rec_s = _span("recommendation-service", "GET /recommend", 200 + i * 50,
                          "error" if i > 4 else "ok", parent=root_s["span_id"],
                          http_status=500 if i > 4 else 200,
                          error="Empty result set from search-service" if i > 4 else None)

            trace = _trace(ts(base, offset + 10), "user-service", 800 + i * 300,
                          "error" if i > 4 else "ok",
                          [root_s, search_s, rec_s], correlation_id=corr)

            if i > 3:
                _error(ts(base, offset + 10), "search-service", "SearchTimeout",
                       "Query timed out waiting for index sync",
                       "search.index.QueryExecutor.search() → elasticsearch.ConnectionTimeout → search.retry.execute()",
                       trace_id=trace["trace_id"], span_id=search_s["span_id"], correlation_id=corr)

            if i > 4:
                _error(ts(base, offset + 10), "recommendation-service", "EmptyResultSet",
                       "Search service returned 0 results for recommendation input",
                       "recommendation.engine.CollaborativeFilter.score() → ValueError: empty input",
                       trace_id=trace["trace_id"], span_id=rec_s["span_id"], correlation_id=corr)

        _metric(ts(base, offset), "search-service", "error_rate",
                min(0.6, 0.01 + i * 0.05), "ratio")
        _metric(ts(base, offset), "recommendation-service", "error_rate",
                min(0.4, 0.005 + max(0, i - 3) * 0.08), "ratio")

    _log(ts(base, incident_start + 120), "alertmanager", "CRITICAL",
         "[FIRING] KafkaConsumerLag > 10000 for 3m on search-service | product-updates topic",
         correlation_id=corr)


# ── Scenario 3: Memory leak in inventory-service ───────────────────────────
# Gradual heap growth → GC pauses → request timeouts → OOMKilled

def scenario_memory_leak():
    corr = "inc-2026-0514-001"
    base = datetime(2026, 5, 14, 3, 0, 0, tzinfo=timezone.utc)

    # Slow build over hours
    for i in range(20):
        offset = i * 600  # every 10 minutes
        heap_mb = 256 + i * 45  # linear growth
        gc_pause = 10 + i * 8 if i > 5 else 10

        _metric(ts(base, offset), "inventory-service", "heap_used_mb", heap_mb, "MB")
        _metric(ts(base, offset), "inventory-service", "gc_pause_ms", gc_pause, "ms")
        _metric(ts(base, offset), "inventory-service", "p99_latency_ms",
                80 + (i ** 2) * 3, "ms")
        _metric(ts(base, offset), "inventory-service", "error_rate",
                min(0.5, 0.005 + max(0, i - 10) * 0.04), "ratio")
        _metric(ts(base, offset), "inventory-service", "throughput_rpm",
                max(200, 1000 - i * 40), "rpm")

        if i > 8:
            _log(ts(base, offset), "inventory-service", "WARN",
                 f"GC pause {gc_pause}ms exceeds threshold (50ms). Heap: {heap_mb}MB / 1024MB",
                 correlation_id=corr if i > 12 else None)

        if i > 14:
            root_s = _span("checkout-service", "POST /checkout", 2000 + i * 200, "error", http_status=504)
            inv_s = _span("inventory-service", "POST /reserve", 1800 + i * 180, "error",
                          parent=root_s["span_id"], http_status=504,
                          error=f"GC stall: allocation failed after {gc_pause}ms pause")
            trace = _trace(ts(base, offset + 30), "checkout-service", 2000 + i * 200, "error",
                          [root_s, inv_s], correlation_id=corr)

            _error(ts(base, offset + 30), "inventory-service", "GCOverhead",
                   f"GC overhead limit exceeded. Heap {heap_mb}MB, pause {gc_pause}ms",
                   "inventory.service.ReservationHandler.reserve() → java.lang.OutOfMemoryError: GC overhead limit exceeded",
                   trace_id=trace["trace_id"], span_id=inv_s["span_id"], correlation_id=corr)

            _log(ts(base, offset + 30), "inventory-service", "ERROR",
                 f"Request timeout: POST /reserve took {1800 + i * 180}ms (GC stall)",
                 trace_id=trace["trace_id"], correlation_id=corr,
                 http_status=504, duration_ms=1800 + i * 180)

    # OOMKilled
    oom_offset = 19 * 600 + 120
    _log(ts(base, oom_offset), "inventory-service", "CRITICAL",
         "Container OOMKilled: memory limit 1024MB exceeded. Pod restarting.",
         correlation_id=corr)
    _log(ts(base, oom_offset + 30), "inventory-service", "INFO",
         "Service restarted. Heap: 180MB / 1024MB. Connections re-established.")

    _log(ts(base, oom_offset - 60), "alertmanager", "CRITICAL",
         "[FIRING] InventoryServiceHeapUsage > 90% for 10m | container at risk of OOM",
         correlation_id=corr)


# ── Scenario 4: Auth token expiry cascading across services ────────────────
# user-service JWT signing key rotates but downstream caches stale key →
# 401s cascade through gateway → all services reject requests

def scenario_auth_cascade():
    corr = "inc-2026-0514-002"
    base = datetime(2026, 5, 14, 8, 15, 0, tzinfo=timezone.utc)

    _log(ts(base, 0), "user-service", "INFO",
         "JWT signing key rotated successfully. New key ID: key-2026-0514-v2")
    _log(ts(base, 5), "gateway-service", "INFO",
         "JWKS cache refreshed. Active keys: [key-2026-0514-v2, key-2026-0514-v1]")

    # Services with stale cached keys start rejecting
    stale_services = ["checkout-service", "payment-service", "inventory-service",
                      "search-service", "notification-service"]

    for i, offset in enumerate(range(30, 330, 20)):
        for svc in stale_services:
            if random.random() > 0.3:  # not all requests fail immediately
                _log(ts(base, offset), svc, "ERROR",
                     f"JWT verification failed: key ID key-2026-0514-v2 not in local JWKS cache. "
                     f"Cached keys: [key-2026-0514-v1]. Returning 401.",
                     correlation_id=corr, http_status=401)

        # Traces showing auth failures
        root_s = _span("gateway-service", "POST /api/checkout", 15, "error", http_status=401)
        auth_s = _span("checkout-service", "middleware.auth", 5, "error",
                       parent=root_s["span_id"], http_status=401,
                       error="JWTVerificationError: unknown key ID")
        trace = _trace(ts(base, offset), "gateway-service", 15, "error",
                      [root_s, auth_s], correlation_id=corr)

        _error(ts(base, offset), "checkout-service", "JWTVerificationError",
               f"Unknown key ID key-2026-0514-v2 in token. JWKS cache stale.",
               "auth.middleware.JWTValidator.verify() → jwt.InvalidKeyError → auth.handler.reject()",
               trace_id=trace["trace_id"], span_id=auth_s["span_id"], correlation_id=corr)

        _metric(ts(base, offset), "gateway-service", "error_rate", min(0.7, 0.1 + i * 0.05), "ratio")
        _metric(ts(base, offset), "gateway-service", "http_401_rate", min(0.65, 0.08 + i * 0.05), "ratio")
        for svc in stale_services:
            _metric(ts(base, offset), svc, "error_rate", min(0.6, 0.05 + i * 0.04), "ratio")

    _log(ts(base, 60), "alertmanager", "CRITICAL",
         "[FIRING] HTTP401Rate > 20% across 5 services for 1m | JWT key rotation suspected",
         correlation_id=corr)

    # Fix: cache refresh propagates
    _log(ts(base, 360), "checkout-service", "INFO",
         "JWKS cache refreshed. Active keys: [key-2026-0514-v2, key-2026-0514-v1]")
    _log(ts(base, 380), "payment-service", "INFO",
         "JWKS cache refreshed. Auth errors recovering.")


# ── Scenario 5: Notification service rate limiting from provider ───────────
# Third-party email provider rate-limits → queue backup → user-visible delays

def scenario_notification_ratelimit():
    corr = "inc-2026-0514-003"
    base = datetime(2026, 5, 14, 11, 0, 0, tzinfo=timezone.utc)

    _log(ts(base, 0), "notification-service", "INFO",
         "Email queue depth: 0. Provider connection healthy.")

    for i, offset in enumerate(range(30, 600, 30)):
        queue_depth = 50 * (i + 1)
        retry_after = 30 + i * 10

        _log(ts(base, offset), "notification-service", "WARN",
             f"Email provider returned 429 Too Many Requests. Retry-After: {retry_after}s. "
             f"Queue depth: {queue_depth}",
             correlation_id=corr, http_status=429)

        _metric(ts(base, offset), "notification-service", "email_queue_depth", queue_depth, "messages")
        _metric(ts(base, offset), "notification-service", "email_send_rate", max(0, 100 - i * 8), "rpm")
        _metric(ts(base, offset), "notification-service", "error_rate", min(0.9, 0.1 + i * 0.07), "ratio")

        if i > 3:
            _error(ts(base, offset), "notification-service", "RateLimitExceeded",
                   f"Provider rate limit: 429 after {queue_depth} queued messages",
                   "notification.provider.SendGridClient.send() → httpx.HTTPStatusError(429) → notification.queue.retry()",
                   correlation_id=corr, count=queue_depth // 10)

            # User-visible: checkout completes but no confirmation email
            _log(ts(base, offset + 5), "checkout-service", "WARN",
                 f"Order confirmation email queued but not sent. Queue position: {queue_depth}",
                 correlation_id=corr)

    _log(ts(base, 300), "alertmanager", "CRITICAL",
         "[FIRING] NotificationQueueDepth > 500 for 5m | email delivery delayed",
         correlation_id=corr)


# ── Generate and write ──────────────────────────────────────────────────────

def main():
    scenario_stripe_outage()
    scenario_kafka_lag()
    scenario_memory_leak()
    scenario_auth_cascade()
    scenario_notification_ratelimit()

    # Sort everything by timestamp
    all_logs.sort(key=lambda x: x["timestamp"])
    all_traces.sort(key=lambda x: x["timestamp"])
    all_metrics.sort(key=lambda x: x["timestamp"])
    all_errors.sort(key=lambda x: x["timestamp"])

    DATA_DIR.mkdir(exist_ok=True)

    with open(DATA_DIR / "logs.json", "w") as f:
        json.dump({"logs": all_logs}, f, indent=2)

    with open(DATA_DIR / "traces.json", "w") as f:
        json.dump({"traces": all_traces}, f, indent=2)

    with open(DATA_DIR / "metrics.json", "w") as f:
        json.dump({"metrics": all_metrics}, f, indent=2)

    with open(DATA_DIR / "errors.json", "w") as f:
        json.dump({"errors": all_errors}, f, indent=2)

    print(f"Generated:")
    print(f"  logs:    {len(all_logs)} entries across {len({l['service'] for l in all_logs})} services")
    print(f"  traces:  {len(all_traces)} traces")
    print(f"  metrics: {len(all_metrics)} datapoints")
    print(f"  errors:  {len(all_errors)} events")
    print(f"  scenarios: 5 (stripe outage, kafka lag, memory leak, auth cascade, notification ratelimit)")
    print(f"  correlation IDs: {sorted({l.get('correlation_id') for l in all_logs if l.get('correlation_id')})}")


if __name__ == "__main__":
    main()

"""OpenTelemetry tracing for the agent system.

Meta-observability: we instrument our own agent system with the same kind of
telemetry the system is designed to investigate. Every LLM call, every MCP tool
call, every graph node becomes a span.

Configure via env vars:
- OTEL_EXPORTER_OTLP_ENDPOINT: send to a collector (e.g. http://localhost:4318)
- OTEL_CONSOLE=1: print spans to stdout (for local dev)
- (neither set) → no-op tracer, zero overhead

Spans emitted:
- agent.<name>.handle — top-level agent invocation
- agent.<name>.<node> — each graph node
- llm.complete — every LLM call (model, tokens, latency, cost)
- mcp.<server>.<tool> — every MCP tool call
"""
from __future__ import annotations

import functools
import logging
import os
from contextlib import contextmanager
from typing import Any, Callable

logger = logging.getLogger(__name__)

_tracer: Any = None


def init_tracing(service_name: str | None = None) -> None:
    """Initialize tracing if settings say so. Safe to call multiple times."""
    global _tracer

    if _tracer is not None:
        return

    from src.core.settings import get_settings
    settings = get_settings()

    if not settings.tracing_enabled:
        return  # no exporter configured — stay no-op

    otlp_endpoint = settings.otel_exporter_otlp_endpoint
    console_export = settings.otel_console
    service_name = service_name or settings.otel_service_name

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        logger.warning("OpenTelemetry packages not installed — tracing disabled")
        return

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")))

    if console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)

    # Auto-inject trace_id + span_id into every log record emitted inside a span.
    # Enables trace ↔ log correlation in Grafana/Tempo.
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        LoggingInstrumentor().instrument(set_logging_format=False)
    except ImportError:
        logger.warning("opentelemetry-instrumentation-logging not installed — log correlation disabled")

    logger.info("OpenTelemetry tracing initialized (otlp=%s, console=%s)", bool(otlp_endpoint), console_export)


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None):
    """Start a span. No-op if tracing isn't initialized."""
    if _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(name) as s:
        if attributes:
            for k, v in attributes.items():
                if v is not None:
                    s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield s


def traced(name: str, attrs_fn: Callable[..., dict] | None = None):
    """Decorator that wraps an async function in a span."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            attrs = attrs_fn(*args, **kwargs) if attrs_fn else {}
            with span(name, attrs):
                return await fn(*args, **kwargs)

        return wrapper

    return decorator

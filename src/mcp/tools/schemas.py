"""Schemas shared by MCP tools and the signal compressors.

Source of truth for field names. Producers (src/mcp/tools/*.py) emit dicts
matching these shapes; the consumer (src/retrieval/signals.py) validates
through these models so a rename or type change fails loudly instead of
silently returning zeros via `.get()` defaults.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LogEntry(_Base):
    id: str = ""
    timestamp: str = ""
    service: str = ""
    level: str = "INFO"
    message: str = "<no message>"
    trace_id: Optional[str] = None
    correlation_id: Optional[str] = None


class MetricPoint(_Base):
    timestamp: str = ""
    service: str = ""
    metric: str
    value: float
    unit: Optional[str] = None


class TraceSpan(_Base):
    span_id: str = ""
    service: Optional[str] = None
    operation: Optional[str] = None
    duration_ms: float = 0
    status: str = "ok"
    parent: Optional[str] = None


class Trace(_Base):
    trace_id: str = ""
    correlation_id: Optional[str] = None
    service: Optional[str] = None
    duration_ms: float = 0
    status: str = "ok"
    spans: list[TraceSpan] = Field(default_factory=list)

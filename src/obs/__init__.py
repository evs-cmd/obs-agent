"""Cross-cutting observability layer.

Public surface — import from ``src.obs`` directly:

    from src import obs
    obs.start(request_id, session_id, query)
    obs.record_mcp(tool, ms, n_bytes)
    obs.finish(answer)
"""
from src.obs.base import (
    RequestCtx,
    finish,
    get,
    record_mcp,
    record_timeout,
    set_route,
    start,
)
from src.obs.cost import BudgetExceeded, record_llm
from src.obs.guard import sanitize_query
from src.obs.redact import redact

__all__ = [
    "BudgetExceeded",
    "RequestCtx",
    "finish",
    "get",
    "record_llm",
    "record_mcp",
    "record_timeout",
    "redact",
    "sanitize_query",
    "set_route",
    "start",
]

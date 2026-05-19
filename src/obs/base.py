"""Per-request observability context.

A ContextVar holds the in-flight ``RequestCtx``. Helpers in this module
mutate it; surrounding application code stays oblivious.

Lifecycle (one cycle per ``POST /ask``):

    start(request_id, session_id, query)   # at endpoint entry
    ... graph runs, helpers record signals ...
    finish(answer)                         # at endpoint exit; flushes event

Why a ContextVar rather than threading ``ctx`` through every call site:
LangGraph runs nodes as asyncio tasks, and ``asyncio.create_task`` snapshots
the current Context, so child tasks see the same ctx without us passing it.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from time import time

_cur: ContextVar["RequestCtx | None"] = ContextVar("obs_ctx", default=None)


@dataclass
class RequestCtx:
    """All telemetry for one request. Serialized verbatim into events.jsonl."""

    request_id: str
    session_id: str
    query: str
    t0: float = field(default_factory=time)
    route: str | None = None
    answer: str | None = None
    total_ms: float | None = None
    llm_calls: list[dict] = field(default_factory=list)
    mcp_calls: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    # Set when an agent-node hit its latency budget. Dashboards filter on
    # this to separate "slow answer" from "incomplete answer".
    degraded: bool = False
    # Node names that timed out; usually 0 or 1 entries per request.
    timeouts: list[str] = field(default_factory=list)


def start(request_id: str, session_id: str, query: str) -> RequestCtx:
    """Open a new request scope. Returns the ctx (callers rarely need it)."""
    ctx = RequestCtx(request_id=request_id, session_id=session_id, query=query)
    _cur.set(ctx)
    return ctx


def get() -> RequestCtx | None:
    """Current request ctx, or ``None`` if outside a request scope."""
    return _cur.get()


def record_mcp(tool: str, ms: float, n_bytes: int) -> None:
    """Append an MCP-call record. No-op outside a request scope."""
    ctx = _cur.get()
    if ctx is not None:
        ctx.mcp_calls.append({"tool": tool, "ms": ms, "bytes": n_bytes})


def set_route(route: str) -> None:
    """Set the route taken by the router. No-op outside a request scope or
    when ``route`` is empty (router hasn't decided yet)."""
    ctx = _cur.get()
    if ctx is not None and route:
        ctx.route = route


def record_timeout(node: str) -> None:
    """Flag the current request as degraded and append the offending node.
    No-op outside a request scope."""
    ctx = _cur.get()
    if ctx is not None:
        ctx.degraded = True
        ctx.timeouts.append(node)


def finish(answer: str) -> None:
    """Close the request scope, write the event row, save the replay snapshot.

    Local imports of ``events``/``replay`` avoid an import cycle (both
    modules import ``RequestCtx`` from this one). The ctx is cleared even
    if a sink fails, so a corrupt sink can't keep stale state alive.

    Each sink wraps its own work in try/except — one failing doesn't
    prevent the other from running.
    """
    from src.obs import events, replay

    ctx = _cur.get()
    if ctx is None:
        return
    ctx.answer = answer
    ctx.total_ms = round((time() - ctx.t0) * 1000, 1)
    try:
        events.write(ctx)
        replay.save(ctx)
    finally:
        _cur.set(None)

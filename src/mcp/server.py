"""Unified MCP server — one FastMCP instance exposing all observability tools.

Tool implementations live in src/mcp/tools/{logs,metrics,traces,errors,correlation}.py.
This module wires them into a single FastMCP server.

Transport:
  - Default `python -m src.mcp.server` runs stdio (for local dev / Studio).
  - `python -m src.mcp.server --sse` runs SSE on port 9000 (for Docker).

In production with separate observability backends (Prometheus, OpenSearch, Sentry),
this monolith would split into per-backend MCP servers. For mock data, one process
is the right size.
"""
from __future__ import annotations

import argparse
import os

from fastmcp import FastMCP

from src.mcp.tools import logs, metrics, traces, errors, correlation

mcp = FastMCP("obs-mcp")

# ─── Register all tools on a single MCP instance ────────────────────────────

# Logs
mcp.tool()(logs.search_logs)
mcp.tool()(logs.get_error_logs)
mcp.tool()(logs.get_log_context)
mcp.tool()(logs.get_logs_by_trace)
mcp.tool()(logs.get_logs_by_correlation)

# Metrics
mcp.tool()(metrics.get_metrics)
mcp.tool()(metrics.get_metric)

# Traces
mcp.tool()(traces.search_traces)
mcp.tool()(traces.get_trace)
mcp.tool()(traces.get_slow_spans)
mcp.tool()(traces.get_traces_by_correlation)

# Errors (Sentry-style)
mcp.tool()(errors.get_errors)
mcp.tool()(errors.get_error_by_trace)
mcp.tool()(errors.get_error_by_correlation)

# Correlation (the smart join)
mcp.tool()(correlation.get_incident_context)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sse", action="store_true", help="Run as SSE server on $MCP_PORT (default 9000)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "9000")))
    parser.add_argument("--host", type=str, default=os.environ.get("MCP_HOST", "0.0.0.0"))
    args = parser.parse_args()

    if args.sse:
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")

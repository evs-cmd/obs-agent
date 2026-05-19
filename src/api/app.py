from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from langgraph.checkpoint.memory import MemorySaver

from src.api.routes import router
from src.core.settings import get_app_config
from src.core.telemetry import init_tracing
from src.graph.builder import build_graph
from src.mcp.hub import ToolHub


class _SafeFormatter(logging.Formatter):
    """Formatter that fills in OTEL trace/span fields when tracing is inactive."""

    _OTEL_DEFAULTS = {"otelTraceID": "0" * 32, "otelSpanID": "0" * 16}

    def format(self, record: logging.LogRecord) -> str:
        for field, default in self._OTEL_DEFAULTS.items():
            if not hasattr(record, field):
                setattr(record, field, default)
        return super().format(record)


JSON_LOG_FORMAT = (
    '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
    '"trace_id":"%(otelTraceID)s","span_id":"%(otelSpanID)s","msg":"%(message)s"}'
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    obs_cfg = get_app_config().get("observability", {})
    init_tracing()  # must run before basicConfig so the logging format string is valid

    use_json = obs_cfg.get("json_logs", True)
    fmt_str = JSON_LOG_FORMAT if use_json else "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    level = getattr(logging, obs_cfg.get("log_level", "INFO"))

    formatter = _SafeFormatter(fmt_str) if use_json else logging.Formatter(fmt_str)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    logger = logging.getLogger(__name__)

    hub = ToolHub()
    checkpointer = MemorySaver()
    app.state.hub = hub
    app.state.checkpointer = checkpointer
    app.state.graph = build_graph(hub, checkpointer=checkpointer)
    logger.info("obs-agent started")
    try:
        yield
    finally:
        await hub.close()


app = FastAPI(
    title="Observability Agent",
    description="AI-powered multi-agent observability assistant",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

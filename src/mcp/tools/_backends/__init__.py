"""Backend selector for MCP tool implementations.

The tool files in ``src/mcp/tools/{logs,metrics,traces,errors,correlation}.py``
are thin dispatchers — they call ``backend().some_fn(...)`` and the
selector here returns either the mock module or the OTel module based
on the ``OBS_BACKEND`` env var.

``otel.py`` is **optional and gitignored** — it can be dropped in by
operators who want to query a live Jaeger/Prom/Loki stack. If it's not
present and ``OBS_BACKEND=otel`` is set anyway, we log a clear warning
and fall back to ``mock`` instead of crashing the app.

Cached: read settings once per process. Override in tests by clearing
``backend.cache_clear()`` after setting env.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from types import ModuleType

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def backend() -> ModuleType:
    """Return the active backend module (mock or otel)."""
    from src.core.settings import get_settings
    name = (get_settings().obs_backend or "mock").lower()
    if name == "otel":
        try:
            from src.mcp.tools._backends import otel as mod
            logger.info("MCP backend: otel (jaeger + prometheus + loki)")
            return mod
        except ImportError as exc:
            logger.warning(
                "OBS_BACKEND=otel but src/mcp/tools/_backends/otel.py is not "
                "available (%s) — falling back to mock.", exc,
            )
    elif name != "mock":
        logger.warning("Unknown OBS_BACKEND=%r — falling back to mock", name)
    from src.mcp.tools._backends import mock as mod
    return mod

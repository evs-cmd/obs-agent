"""Shared fixtures for eval tests.

Side-effect for the DuckDB dashboard: every eval test runs inside an
``obs.start / obs.finish`` scope. Each test's LLM + MCP calls land in
``events.jsonl`` exactly like a production ``POST /ask`` would, tagged
with route ``eval-routing`` or ``eval-synth`` so the existing
``route_stats`` / ``model_costs`` views slice eval cost + latency next
to real traffic. Set ``OBS_EVENTS_PATH`` to send them elsewhere.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from src.graph.registry import load_agents as _load_agents
from src.mcp.hub import ToolHub
from src.obs import base as obs

GOLDEN_PATH = Path(__file__).parent / "golden.json"


def _load_golden() -> list[dict]:
    with open(GOLDEN_PATH) as f:
        return json.load(f)["queries"]


def all_cases() -> list[dict]:
    return _load_golden()


def synth_cases() -> list[dict]:
    return [c for c in _load_golden() if c["id"].startswith("synth-")]


def case_id(case: dict) -> str:
    return case["id"]


@pytest_asyncio.fixture
async def hub():
    h = ToolHub()
    yield h
    await h.close()


@pytest.fixture
def agents():
    return _load_agents()


# ─── Observability sink: route eval cost/latency into events.jsonl ────────
#
# Why an autouse fixture instead of wrapping each test by hand:
#   The agent code already records LLM + MCP calls onto whatever
#   ``RequestCtx`` is bound to the current context. Opening one here per
#   test means every existing instrumented call site flows through
#   unchanged — zero modifications to src/.

def _route_for(test_id: str) -> str:
    """Tag eval rows so dashboard panels can filter them apart from prod."""
    if "synthesis" in test_id or "synth" in test_id:
        return "eval-synth"
    if "routing" in test_id or "router" in test_id:
        return "eval-routing"
    return "eval-other"


@pytest.fixture(autouse=True)
def _obs_scope(request):
    """Open one RequestCtx per eval test, flush to events.jsonl on exit."""
    test_id = request.node.nodeid  # e.g. "evals/test_routing.py::test_router_classification[router-001]"
    case = getattr(request.node, "callspec", None)
    query = ""
    if case is not None:
        params = getattr(case, "params", {}) or {}
        c = params.get("case") or {}
        if isinstance(c, dict):
            query = c.get("query", "")
    obs.start(
        request_id=f"eval-{uuid.uuid4().hex[:8]}",
        session_id=test_id,
        query=query or test_id,
    )
    obs.set_route(_route_for(test_id))
    try:
        yield
    finally:
        # Telemetry must never break a test — swallow sink errors. We pass
        # the test id as the "answer" because the real model output may be
        # unavailable (router-only tests, parametrize failures, etc.).
        try:
            obs.finish(answer=f"<eval:{test_id}>")
        except Exception:
            pass

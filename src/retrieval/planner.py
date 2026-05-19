"""Query planner — picks per-source data budgets for an observability query.

Two implementations live here:

  - ``plan_query(query)``        — sync, keyword-based. Free, deterministic, brittle.
  - ``plan_query_llm(query)``    — async, single-LLM-call. Costs ~$0.0002 and
                                   ~200-400ms but handles novel phrasings.

The async ``plan(query)`` wrapper picks one based on settings, so callers
write ``await plan(q)`` once and switch implementations via config.

LLM planner failures (timeout, malformed JSON, validation error) fall back
to the keyword planner silently — observability queries must never fail
because the planner LLM had a bad day.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from src.core.settings import get_app_config, get_settings
from src.llm.client import llm_complete
from src.llm.prompts import render

logger = logging.getLogger(__name__)


class Plan(BaseModel):
    """Per-source data budgets + classified intent.

    Field bounds keep the LLM planner honest: a runaway "fetch 100000 logs"
    response gets clamped to something sane at validation time.
    """

    intent: Literal[
        "error-focused",
        "latency-focused",
        "health-focused",
        "incident-investigation",
        "default",
    ] = "default"
    log_limit: int = Field(default=50, ge=0, le=500)
    metric_limit: int = Field(default=50, ge=0, le=500)
    trace_limit: int = Field(default=5, ge=0, le=50)
    error_limit: int = Field(default=20, ge=0, le=200)


# Keyword → intent map. Order matters: earlier rules win.
_INTENTS: list[tuple[str, tuple[str, ...], dict[str, int]]] = [
    ("error-focused",
     ("error", "failing", "broken", "crash", "exception"),
     {"log_limit": 100, "metric_limit": 30,  "trace_limit": 5,  "error_limit": 50}),
    ("latency-focused",
     ("slow", "latency", "p99", "p95", "timeout"),
     {"log_limit": 30,  "metric_limit": 100, "trace_limit": 10, "error_limit": 10}),
    ("health-focused",
     ("health", "status", "rate", "throughput", "cpu", "memory"),
     {"log_limit": 20,  "metric_limit": 150, "trace_limit": 2,  "error_limit": 10}),
    ("incident-investigation",
     ("incident", "investigate", "outage", "why is"),
     {"log_limit": 100, "metric_limit": 100, "trace_limit": 10, "error_limit": 50}),
]


def plan_query(query: str) -> Plan:
    """Keyword-driven budgets. Pure heuristic, no LLM, no I/O."""
    q = query.lower()
    for intent, kws, budgets in _INTENTS:
        if any(kw in q for kw in kws):
            return Plan(intent=intent, **budgets)
    return Plan()


async def plan_query_llm(query: str, *, model: str | None = None) -> Plan:
    """Ask an LLM to classify the query and pick data budgets.

    Uses JSON-object response format + Pydantic validation. On any failure
    (network, malformed JSON, schema mismatch, bound violation) falls back
    to the keyword planner so callers never have to handle a failed plan.

    Cost telemetry: goes through ``llm_complete``, so the call shows up in
    ``events.jsonl.llm_calls`` automatically.
    """
    model = model or get_settings().llm_planner_model
    try:
        response = await llm_complete(
            messages=[
                {"role": "system", "content": render("planner_system")},
                {"role": "user", "content": query[:1000]},
            ],
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        return Plan.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        logger.warning("plan_query_llm validation failed (%s) — falling back", exc)
    except Exception as exc:  # network, timeout, provider error
        logger.warning("plan_query_llm call failed (%s) — falling back", exc)
    return plan_query(query)


async def plan(query: str) -> Plan:
    """Dispatch to keyword or LLM planner based on ``settings.llm_planner_enabled``.

    Single entry point for agents — flip the setting to A/B the two
    implementations without touching agent code.
    """
    if get_settings().llm_planner_enabled:
        return await plan_query_llm(query)
    return plan_query(query)


# ─── Service detection ────────────────────────────────────────────────────────


def known_services() -> list[str]:
    """Load the canonical service list from config (incident.services). Empty
    list when unconfigured — callers should treat 'unknown' as a valid value."""
    cfg = get_app_config()
    return list(cfg.get("incident", {}).get("services", []))


def detect_service(query: str, services: list[str] | None = None) -> str:
    """Find which known service the query is about. Falls back to the first
    configured service, then 'unknown', so callers always get a usable string."""
    services = services if services is not None else known_services()
    q = query.lower()
    for svc in services:
        if svc.replace("-service", "").replace("-", " ") in q:
            return svc
    return services[0] if services else "unknown"

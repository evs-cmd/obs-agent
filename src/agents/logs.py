"""Deterministic LogsAgent.

Replaces the inherited ReAct loop with a fixed pipeline:

    plan(query) → parallel fetch(logs, errors) → summarize → 1 LLM synth

vs. the old ReAct path which made 3-5 LLM calls per query (LLM picks tools,
gets data, decides next tool, ...). The deterministic path is ~4× cheaper,
~3× faster, and goes through ``llm_complete`` so cost telemetry works
end-to-end.

Escape hatch: when the planner returns ``intent="default"`` (the keyword
heuristic didn't recognize the query, or the LLM planner said so), we fall
through to ``BaseAgent.run_react``. The ReAct loop is bounded by
``react_recursion_limit`` so the worst case is still capped.

Toggle the LLM-based planner with ``LLM_PLANNER_ENABLED=true`` — same
agent code, different planner under the hood.
"""
from __future__ import annotations

import asyncio
import logging

from src.agents.base import BaseAgent
from src.llm.client import llm_complete
from src.llm.prompts import render
from src.mcp.hub import ToolHub
from src.retrieval.planner import detect_service, plan
from src.retrieval.signals import summarize_logs

logger = logging.getLogger(__name__)


class LogsAgent(BaseAgent):
    async def handle(self, query: str, hub: ToolHub) -> str:
        chosen_plan = await plan(query)

        # Unknown intent → fall through to ReAct. The deterministic pipeline
        # only fits queries the planner recognizes; for exploratory queries
        # ("look up the auth jwks runbook then check related failures") let
        # the LLM pick tools dynamically — bounded by react_recursion_limit.
        if chosen_plan.intent == "default":
            logger.info("LogsAgent: planner returned default → ReAct fallback")
            return await self.run_react(query, hub)

        service = detect_service(query)
        logger.info(
            "LogsAgent deterministic: intent=%s service=%s budgets=(log=%d, err=%d)",
            chosen_plan.intent, service, chosen_plan.log_limit, chosen_plan.error_limit,
        )

        # Parallel fetch — both calls are independent, each goes through
        # hub.call → obs.record_mcp.
        logs_task = hub.call(
            "search_logs",
            {"service": service, "limit": chosen_plan.log_limit},
        )
        errors_task = hub.call(
            "get_errors",
            {"service": service, "limit": chosen_plan.error_limit},
        )
        logs_raw, errors_raw, degraded = await _gather_resilient(logs_task, errors_task)

        # Compression — pure functions, ~ms each. Cuts ~100 raw rows down to
        # clusters + level histogram + example IDs the LLM can cite.
        logs_summary = summarize_logs(logs_raw or [])
        errors_summary = summarize_logs(errors_raw or [])

        # One synthesis call. Bounded output. Cost lands in events.jsonl.
        prompt = render(
            "logs_synthesis",
            query=query,
            service=service,
            plan=chosen_plan,
            logs=logs_summary,
            errors=errors_summary,
            degraded=degraded,
        )
        response = await llm_complete(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            model=self.model,
            temperature=0.3,
            max_tokens=600,
        )
        return response.choices[0].message.content or ""


async def _gather_resilient(*tasks) -> tuple:
    """asyncio.gather but a single failing task doesn't lose the rest.

    Returns ``(*results, degraded)`` where ``results`` are per-task payload-
    or-None and ``degraded`` is True iff any task failed. Lets synthesis
    proceed with whatever signals were retrievable — a stuck logs backend
    shouldn't blank out the error analysis.
    """
    results = await asyncio.gather(*tasks, return_exceptions=True)
    degraded = any(isinstance(r, BaseException) for r in results)
    if degraded:
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("LogsAgent fetch failed: %r", r)
    cleaned = tuple(None if isinstance(r, BaseException) else r for r in results)
    return (*cleaned, degraded)

"""Incident investigation agent.

Single-file layout with sections:
    Helpers        — pure functions + reducers
    State          — pydantic IncidentState with reducers on parallel-write fields
    Routing        — conditional-edge functions (fan-out via Send, critique loop)
    Agent          — IncidentAgent class: graph construction + node bodies + entry point

Graph shape:

    plan → gather_context ─┬─ refine_plan ───┐
                           │                 └→ gather_context  (loop, capped by max_iterations)
                           ├─ Send(inspect_trace × N)   ─┐
                           ├─ Send(inspect_service × M) ─┤→ synthesize ─┬─ END
                           └─ synthesize                 ─┘             └─ self_critique → synthesize (capped)

Why this shape:
- pydantic state with reducers means parallel branches (one Send per error trace,
  one per related service) can each write a partial `drill_down` and the runtime
  merges them via the `add` list reducer — no clobbering, no asyncio.gather hiding
  parallelism inside a single node.
- the planner is an explicit node so Studio shows it and `refine_plan` can re-enter
  it without leaving the graph.
- the critique loop is capped by `critique_iteration` to prevent infinite revision
  when confidence stays low.

Send-payload nuance: when a node is invoked via `Send(name, dict)`, LangGraph
passes the *raw dict* to the node — not a validated pydantic instance. The two
inspect_* nodes therefore accept `payload: dict` and use dict access. All other
nodes receive `state: IncidentState`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from operator import add
from pathlib import Path
from typing import Annotated, Any

from jinja2 import Environment, FileSystemLoader
from langgraph.graph import END, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from src.agents.base import BaseAgent
from src.core.settings import get_app_config, get_settings
from src.core.telemetry import span
from src.llm.client import llm_complete
from src.llm.schemas import CritiqueVerdict, IncidentSynthesis
from src.llm.validation import strip_hallucinations, validate_citations
from src.retrieval.planner import Plan, detect_service, plan_query
from src.retrieval.signals import (
    build_dependency_graph,
    summarize_logs,
    summarize_metrics,
    summarize_traces,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
logger = logging.getLogger(__name__)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _truncate(data: Any, max_items: int = 10) -> Any:
    if isinstance(data, list):
        return data[:max_items]
    if isinstance(data, dict):
        return {k: _truncate(v, max_items) for k, v in list(data.items())[:max_items]}
    return data


# ─── State ──────────────────────────────────────────────────────────────────
#
# `Plan` and `plan_query` live in src.retrieval.planner — same data budgets
# are now consumed by deterministic LogsAgent / MetricsAgent / etc. Migration
# left the imports above; this comment marks the historical location.


class IncidentState(BaseModel):
    """Pydantic state for the incident investigation subgraph.

    `drill_down` uses the `add` list reducer so parallel branches
    (one Send per error trace, one per related service) accumulate
    findings instead of clobbering each other.
    """

    # ── inputs (set at entry, never re-written) ──
    query: str
    service: str = "unknown"

    # ── planner output (refine_plan re-writes) ──
    plan: Plan = Field(default_factory=Plan)
    iteration_count: int = 0
    max_iterations: int = 2

    # ── gathered evidence (replaced wholesale on re-gather) ──
    context: dict[str, Any] = Field(default_factory=dict)
    signals: dict[str, Any] = Field(default_factory=dict)

    # ── parallel fan-out accumulator ──
    drill_down: Annotated[list[dict[str, Any]], add] = Field(default_factory=list)

    # ── synthesis + critique ──
    synthesis: dict[str, Any] = Field(default_factory=dict)
    critique_iteration: int = 0
    output: str = ""


# ─── Agent ──────────────────────────────────────────────────────────────────

class IncidentAgent(BaseAgent):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        app_cfg = get_app_config()
        incident_cfg = app_cfg.get("incident", {})
        prompts_cfg = app_cfg.get("prompts", {})

        self.services: list[str] = incident_cfg.get("services", [])
        self.tool_timeout: float = incident_cfg.get("tool_timeout", 5)
        self.max_iterations: int = incident_cfg.get("max_iterations", 2)
        self.max_critique_passes: int = incident_cfg.get("max_critique_passes", 1)

        settings = get_settings()
        self.enable_self_critique: bool = settings.enable_self_critique
        self.self_critique_threshold: float = settings.self_critique_threshold

        # RAG + Memory: production-ready slots, unimplemented for the demo.
        self.memory = None
        self.rag = None

        self._system_template = prompts_cfg.get("incident_system", "incident_system.j2")
        self._synthesis_template = prompts_cfg.get("incident_synthesis", "incident_synthesis.j2")

        self._hub: Any = None
        self._graph: Any = None

    # ── entry point ────────────────────────────────────────────────────────

    async def handle(self, query: str, hub: Any) -> str:
        self._hub = hub
        if self._graph is None:
            self._graph = self._build_graph()

        initial = IncidentState(query=query, max_iterations=self.max_iterations)
        result = await self._graph.ainvoke(initial)

        # LangGraph returns a dict (not the pydantic instance) on ainvoke.
        output = result["output"] if isinstance(result, dict) else result.output
        if self.memory and output:
            await self.memory.store(f"Investigation: {query}\nFindings: {output[:500]}")
        return output

    # ── graph construction ────────────────────────────────────────────────

    def _build_graph(self) -> Any:
        sg = StateGraph(IncidentState)

        sg.add_node("plan", self._plan_node)
        sg.add_node("gather_context", self._gather_context)
        sg.add_node("refine_plan", self._refine_plan)
        sg.add_node("inspect_trace", self._inspect_trace)
        sg.add_node("inspect_service", self._inspect_service)
        sg.add_node("synthesize", self._synthesize)
        sg.add_node("self_critique", self._self_critique)

        sg.set_entry_point("plan")
        sg.add_edge("plan", "gather_context")

        # gather_context routes to refine, fan-out, or straight to synthesis
        sg.add_conditional_edges(
            "gather_context",
            self._route_after_gather,
            ["refine_plan", "inspect_trace", "inspect_service", "synthesize"],
        )
        sg.add_edge("refine_plan", "gather_context")

        # Parallel branches converge at synthesize
        sg.add_edge("inspect_trace", "synthesize")
        sg.add_edge("inspect_service", "synthesize")

        # Critique loop: capped by critique_iteration
        sg.add_conditional_edges(
            "synthesize",
            self._should_critique,
            {"critique": "self_critique", "end": END},
        )
        sg.add_edge("self_critique", "synthesize")

        return sg.compile()

    # ── routing ───────────────────────────────────────────────────────────

    def _route_after_gather(self, state: IncidentState) -> list[Send] | str:
        """Decide what happens after evidence is gathered.

        1. No logs + retries left  → refine_plan (loop with bigger budgets)
        2. Errors or related svcs   → parallel fan-out (one Send each)
        3. Nothing actionable       → straight to synthesize
        """
        logs = state.signals.get("logs") or []
        if not logs and state.iteration_count < state.max_iterations:
            logger.info("IncidentAgent: low signal, iteration=%d → refine_plan", state.iteration_count)
            return "refine_plan"

        traces = state.context.get("traces", [])
        error_traces = [t for t in traces if t.get("status") == "error"]
        related = [s for s in state.context.get("services", []) if s != state.service]

        if not error_traces and not related:
            logger.info("IncidentAgent: no error traces / no related services → synthesize")
            return "synthesize"

        # Carry the slice each branch needs in the Send payload. The reducer
        # on `drill_down` will merge each branch's partial write.
        base = {
            "query": state.query,
            "service": state.service,
            "context": state.context,
            "signals": state.signals,
        }
        sends: list[Send] = []
        for t in error_traces[:5]:
            sends.append(Send("inspect_trace", {**base, "target_trace_id": t["trace_id"]}))
        for svc in related:
            sends.append(Send("inspect_service", {**base, "target_service": svc}))

        logger.info("IncidentAgent: fanning out %d investigation branches", len(sends))
        return sends or "synthesize"

    def _should_critique(self, state: IncidentState) -> str:
        """Conditional edge after synthesize. Returns 'critique' or 'end'."""
        if not self.enable_self_critique:
            return "end"
        if state.critique_iteration >= self.max_critique_passes:
            return "end"
        confidence = state.synthesis.get("confidence") if state.synthesis else None
        if confidence is None or confidence >= self.self_critique_threshold:
            return "end"
        logger.info(
            "IncidentAgent: confidence %.2f < threshold %.2f → critique (pass %d)",
            confidence, self.self_critique_threshold, state.critique_iteration + 1,
        )
        return "critique"

    # ── nodes ─────────────────────────────────────────────────────────────

    async def _plan_node(self, state: IncidentState) -> dict:
        plan = plan_query(state.query)
        service = detect_service(state.query, self.services)
        logger.info(
            "IncidentAgent: planned intent=%s service=%s budgets=%s",
            plan.intent, service, plan.model_dump(),
        )
        return {"plan": plan, "service": service}

    async def _gather_context(self, state: IncidentState) -> dict:
        plan = state.plan
        with span("agent.incident.gather_context", {
            "service": state.service, "iteration": state.iteration_count,
        }):
            context = await self._safe_call("get_incident_context", {
                "service": state.service,
                "log_limit": plan.log_limit,
                "trace_limit": plan.trace_limit,
                "error_limit": plan.error_limit,
                "metric_limit": plan.metric_limit,
            })

        # Retrieval layer: compress raw data into signals before the LLM sees it.
        signals: dict[str, Any] = {}
        if isinstance(context, dict):
            traces = context.get("traces", []) or []
            signals["logs"] = summarize_logs(context.get("logs", []), top_k=5)
            signals["traces"] = summarize_traces(traces, top_k=5)
            # Service-call graph from span parent links — gives the LLM cascade
            # direction without having to re-infer it from raw spans.
            signals["dependency_graph"] = build_dependency_graph(traces)
            per_service_metrics = context.get("metrics", {}) or {}
            signals["metrics"] = {
                svc: summarize_metrics(rows)
                for svc, rows in per_service_metrics.items()
                if isinstance(rows, list)
            }

        return {"context": context, "signals": signals}

    async def _refine_plan(self, state: IncidentState) -> dict:
        """Bump data budgets and re-enter gather_context."""
        new_plan = state.plan.model_copy(update={
            "log_limit": state.plan.log_limit + 20,
            "trace_limit": state.plan.trace_limit + 5,
        })
        logger.info(
            "IncidentAgent: refined budgets logs=%d traces=%d (iter=%d)",
            new_plan.log_limit, new_plan.trace_limit, state.iteration_count + 1,
        )
        return {"plan": new_plan, "iteration_count": state.iteration_count + 1}

    # Note: inspect_trace / inspect_service receive raw dicts because Send
    # bypasses pydantic validation on the payload.
    async def _inspect_trace(self, payload: dict) -> dict:
        """Fan-out branch: deep-dive one error trace."""
        trace_id = payload.get("target_trace_id")
        if not trace_id:
            return {}
        with span("agent.incident.inspect_trace", {"trace_id": trace_id}):
            trace = await self._safe_call("get_trace", {"trace_id": trace_id})

        if not isinstance(trace, dict) or "spans" not in trace:
            return {}

        spans = trace["spans"]
        error_spans = [s for s in spans if s.get("status") == "error"]
        slowest = max(spans, key=lambda s: s.get("duration_ms", 0), default=None)
        if not slowest:
            return {}

        return {"drill_down": [{
            "kind": "trace_bottleneck",
            "trace_id": trace.get("trace_id"),
            "bottleneck_service": slowest.get("service"),
            "bottleneck_operation": slowest.get("operation"),
            "duration_ms": slowest.get("duration_ms"),
            "error_count": len(error_spans),
            "errors": [s.get("error") for s in error_spans if s.get("error")],
        }]}

    async def _inspect_service(self, payload: dict) -> dict:
        """Fan-out branch: check one related service for cascade degradation."""
        target = payload.get("target_service")
        if not target:
            return {}
        with span("agent.incident.inspect_service", {"service": target}):
            metrics = await self._safe_call("get_metrics", {"service": target})

        if not isinstance(metrics, list):
            return {}
        error_rates = [m for m in metrics if m.get("metric") == "error_rate"]
        if not error_rates:
            return {}
        latest = max(error_rates, key=lambda m: m["timestamp"])
        earliest = min(error_rates, key=lambda m: m["timestamp"])
        return {"drill_down": [{
            "kind": "cascade_check",
            "service": target,
            "degraded": latest["value"] > earliest["value"] * 3,
            "error_rate_first": earliest["value"],
            "error_rate_latest": latest["value"],
        }]}

    async def _synthesize(self, state: IncidentState) -> dict:
        ctx = state.context
        signals = state.signals
        memory_context = "No previous investigations."

        template_ctx = {
            "query": state.query,
            "service": state.service,
            "trace_id": ctx.get("trace_id", "unknown"),
            "correlation_id": ctx.get("correlation_id", "unknown"),
            "services": json.dumps(ctx.get("services", []), default=str),
            "root_service": ctx.get("root_service", "unknown"),
            # Compressed signals (retrieval layer output) — primary evidence
            "log_summary": json.dumps(signals.get("logs", {}), indent=2, default=str),
            "trace_summary": json.dumps(signals.get("traces", {}), indent=2, default=str),
            "metric_summary": json.dumps(signals.get("metrics", {}), indent=2, default=str),
            "dependency_graph": json.dumps(signals.get("dependency_graph", {}), indent=2, default=str),
            # Raw context (truncated) — for citing specific IDs
            "errors": json.dumps(_truncate(ctx.get("errors", [])), indent=2, default=str),
            "raw_traces": json.dumps(_truncate(ctx.get("traces", []), max_items=5), indent=2, default=str),
            "raw_logs": json.dumps(_truncate(ctx.get("logs", []), max_items=10), indent=2, default=str),
            "drill_down": json.dumps(_truncate(state.drill_down), indent=2, default=str),
            "memories": memory_context,
        }

        system_prompt = _jinja_env.get_template(self._system_template).render()
        user_prompt = _jinja_env.get_template(self._synthesis_template).render(**template_ctx)

        with span("agent.incident.synthesize", {"model": self.model}):
            response = await llm_complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"

        try:
            synthesis = IncidentSynthesis.model_validate_json(raw_content)
        except Exception as e:
            logger.error("IncidentAgent: synthesis parse failed — %s", e)
            return {"output": raw_content}

        report = validate_citations(synthesis, ctx)
        if report.invalid_citations:
            logger.warning(
                "IncidentAgent: stripped %d hallucinated citations (rate=%.2f)",
                len(report.invalid_citations), report.hallucination_rate,
            )
            synthesis = strip_hallucinations(synthesis, report)

        return {
            "output": synthesis.to_markdown(),
            "synthesis": synthesis.model_dump(),
        }

    async def _self_critique(self, state: IncidentState) -> dict:
        synth_data = state.synthesis
        if not synth_data:
            return {"critique_iteration": state.critique_iteration + 1}

        try:
            synthesis = IncidentSynthesis.model_validate(synth_data)
        except Exception as e:
            logger.warning("IncidentAgent: critique skipped — could not reparse synthesis: %s", e)
            return {"critique_iteration": state.critique_iteration + 1}

        ctx = state.context
        signals = state.signals
        critique_ctx = {
            "query": state.query,
            "original_confidence": f"{synthesis.confidence:.2f}",
            "synthesis_markdown": synthesis.to_markdown(),
            "services": json.dumps(ctx.get("services", []), default=str),
            "root_service": ctx.get("root_service", "unknown"),
            "log_summary": json.dumps(signals.get("logs", {}), indent=2, default=str),
            "trace_summary": json.dumps(signals.get("traces", {}), indent=2, default=str),
            "metric_summary": json.dumps(signals.get("metrics", {}), indent=2, default=str),
            "errors": json.dumps(_truncate(ctx.get("errors", [])), indent=2, default=str),
        }

        system_prompt = _jinja_env.get_template("critique_system.j2").render()
        user_prompt = _jinja_env.get_template("critique_user.j2").render(**critique_ctx)

        try:
            with span("agent.incident.self_critique", {"model": self.model}):
                response = await llm_complete(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    model=self.model,
                    response_format={"type": "json_object"},
                )
            critique = CritiqueVerdict.model_validate_json(response.choices[0].message.content or "{}")
        except Exception as e:
            logger.warning("IncidentAgent: critique LLM call failed: %s", e)
            return {"critique_iteration": state.critique_iteration + 1}

        logger.info(
            "IncidentAgent: critique verdict=%s revised_confidence=%.2f",
            critique.verdict, critique.revised_confidence,
        )
        return {
            "output": state.output + critique.to_markdown(),
            "critique_iteration": state.critique_iteration + 1,
        }

    # ── helpers ──────────────────────────────────────────────────────────
    # _detect_service moved to src.retrieval.planner.detect_service so the
    # deterministic LogsAgent / MetricsAgent / etc. can reuse the same logic.

    async def _safe_call(self, tool: str, args: dict) -> Any:
        try:
            return await asyncio.wait_for(
                self._hub.call(tool, args),
                timeout=self.tool_timeout,
            )
        except Exception as e:
            logger.error("IncidentAgent: %s failed — %s", tool, e)
            return {"error": str(e)}

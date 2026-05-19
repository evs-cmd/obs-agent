"""Top-level graph builder."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph

from src import obs
from src.agents.base import BaseAgent
from src.graph.registry import load_agents
from src.graph.router import route_to_agent, router_node
from src.graph.state import AgentState
from src.core.telemetry import span
from src.mcp.hub import ToolHub

logger = logging.getLogger(__name__)

# Returned as the agent_output when a node hits its latency budget. Kept
# short and oncall-friendly: the structured event row (obs/events.jsonl)
# has the full timing breakdown — this string is just what the user sees
# in the SSE stream.
_DEGRADED_TEMPLATE = (
    "_Partial result: the {agent} agent exceeded its {timeout_s:.1f}s budget "
    "and was cut short. Try a more specific question, or check "
    "`events.jsonl` for which backend stalled._"
)

# Shown when the router classifies a query as out_of_scope (or falls back
# to out_of_scope on low confidence). Kept short and explicit so the user
# understands what this system *does* handle, not just what it doesn't.
_OUT_OF_SCOPE_MESSAGE = (
    "I can only help with observability questions about this system — "
    "logs, metrics, traces, or incident investigation. "
    "Try asking about a specific service, error, latency, or live incident."
)


async def out_of_scope_node(state: AgentState) -> dict:
    """Terminal node for queries the router rejected as non-observability.

    No LLM call, no MCP call — just a fixed message. Records the reasoning
    so the off-topic miss is greppable in `events.jsonl`.
    """
    reasoning = state.get("route_reasoning", "")
    logger.info(
        "out_of_scope: '%s' rejected (router reasoning: %s)",
        state["user_query"][:80], reasoning,
    )
    return {
        "agent_output": _OUT_OF_SCOPE_MESSAGE,
        "messages": [AIMessage(content=_OUT_OF_SCOPE_MESSAGE)],
    }


def _make_node(agent_name: str, agent: BaseAgent, hub: ToolHub):
    """Build a graph node that runs an agent under a hard latency budget.

    Budget source: ``agent.node_timeout_ms`` (per-agent override → settings
    default). Setting it to 0 disables the cap. On timeout we record the
    event and return a degraded answer rather than failing the whole
    request — the SSE stays a 200 with a partial body, the failure mode
    is visible in telemetry, and the next session retry can succeed.
    """
    async def node(state: AgentState) -> dict:
        attrs = {
            "agent.name": agent_name,
            "agent.model": agent.model,
            "agent.query_length": len(state["user_query"]),
            "agent.timeout_ms": agent.node_timeout_ms,
        }
        with span(f"agent.{agent_name}.handle", attrs):
            coro = agent.handle(state["user_query"], hub)
            timeout_s = agent.node_timeout_ms / 1000.0
            try:
                if agent.node_timeout_ms > 0:
                    output = await asyncio.wait_for(coro, timeout=timeout_s)
                else:
                    output = await coro
            except asyncio.TimeoutError:
                logger.warning(
                    "agent %s exceeded %dms budget — degrading",
                    agent_name, agent.node_timeout_ms,
                )
                obs.record_timeout(agent_name)
                output = _DEGRADED_TEMPLATE.format(
                    agent=agent_name, timeout_s=timeout_s
                )
        return {"agent_output": output, "messages": [AIMessage(content=output)]}
    return node


def build_graph(
    hub: ToolHub | None = None,
    checkpointer: Any = None,
) -> Any:
    """Build the top-level routing graph.

    Args:
        hub: Shared ToolHub for MCP tool dispatch + JIT selection. Created lazily for Studio mode.
        checkpointer: Optional LangGraph checkpointer for session persistence.
            Pass `MemorySaver()` for in-process sessions, or None to disable.
            When set, each `ainvoke` must include `config={"configurable": {"thread_id": ...}}`.
    """
    if hub is None:
        hub = ToolHub()

    agents = load_agents()

    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.add_node("out_of_scope", out_of_scope_node)

    for name, agent in agents.items():
        graph.add_node(name, _make_node(name, agent, hub))

    graph.set_entry_point("router")

    # Route map: every concrete route name → its node. `out_of_scope` is a
    # first-class destination, not a fallback agent.
    route_map = {name: name for name in agents}
    route_map["out_of_scope"] = "out_of_scope"
    graph.add_conditional_edges("router", route_to_agent, route_map)

    graph.add_edge("out_of_scope", END)
    for name in agents:
        graph.add_edge(name, END)

    return graph.compile(checkpointer=checkpointer) if checkpointer else graph.compile()


# Module-level graph export — handy for introspection / tooling that
# wants a no-checkpointer instance.
graph = build_graph()

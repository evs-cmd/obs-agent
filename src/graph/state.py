from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages as _lc_add_messages
from langchain_core.messages import BaseMessage

# Per-session message-history cap.
#
# Without this, ``add_messages`` accumulates indefinitely across multi-turn
# sessions (the checkpointer reloads + appends every turn). At 30 turns the
# state object is several MB; at 100 turns the checkpointer write becomes a
# real latency hit and any node that reads ``state["messages"]`` blows up the
# LLM context window.
#
# Why a wrapper, not a node: applying the cap inside the reducer means it
# runs at *every* merge, in *every* node, for *every* graph — no caller can
# forget to truncate.
MAX_MESSAGES = 20


def add_messages(left: list[BaseMessage], right: list[BaseMessage]) -> list[BaseMessage]:
    """Wraps LangGraph's ``add_messages`` reducer with a tail-keep cap."""
    merged = _lc_add_messages(left, right)
    if len(merged) > MAX_MESSAGES:
        return merged[-MAX_MESSAGES:]
    return merged


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    route: str
    route_reasoning: str
    user_query: str
    agent_output: str
    investigation: dict[str, Any]
    memory_context: list[str]
    rag_context: list[str]

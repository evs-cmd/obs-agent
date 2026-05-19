from __future__ import annotations

import logging
from abc import ABC
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from src.core.settings import get_settings
from src.llm.prompts import render
from src.mcp.hub import ToolHub

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Per-query JIT tool selection: hub picks top-K + always-on tools."""

    # Default recursion cap for the ReAct loop. LangGraph's default is 25;
    # we drop it to 6 so a pathological query maxes out around
    # 6 LLM + 6 tool calls (~$0.10 on gpt-4o) instead of ~$0.50. Override per
    # agent via ``react_recursion_limit:`` in ``config/app.yaml``.
    DEFAULT_RECURSION_LIMIT = 6

    def __init__(self, config: dict[str, Any]) -> None:
        self.model: str = config["model"]
        self.description: str = config["description"]
        self.tool_k: int = int(config.get("tool_k", 5))
        self.always_on_tools: list[str] = list(config.get("always_on_tools", []))
        self.react_recursion_limit: int = int(
            config.get("react_recursion_limit", self.DEFAULT_RECURSION_LIMIT)
        )
        # Per-agent latency budget. Config wins; otherwise fall back to the
        # global default in Settings.node_timeout_ms.
        self.node_timeout_ms: int = int(
            config.get("node_timeout_ms", get_settings().node_timeout_ms)
        )
        if "system_prompt_template" in config:
            self.system_prompt = render(config["system_prompt_template"])
        else:
            self.system_prompt = config["system_prompt"]

    async def handle(self, query: str, hub: ToolHub) -> str:
        return await self.run_react(query, hub)

    def _make_llm(self) -> ChatOpenAI:
        s = get_settings()
        return ChatOpenAI(model=self.model, temperature=0.3, api_key=s.openai_api_key)

    async def run_react(self, query: str, hub: ToolHub) -> str:
        tools = await hub.topk(query, k=self.tool_k, always_on=self.always_on_tools)
        logger.debug("%s bound %d tools: %s", self.__class__.__name__, len(tools), [t.name for t in tools])
        agent = create_react_agent(self._make_llm(), tools, prompt=self.system_prompt)
        result = await agent.ainvoke(
            {"messages": [("user", query)]},
            config={"recursion_limit": self.react_recursion_limit},
        )
        return result["messages"][-1].content or ""

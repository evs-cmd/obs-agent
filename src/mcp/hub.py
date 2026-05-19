"""Tool hub — single layer over `langchain-mcp-adapters`.

One class owns the MCP connection, the tool catalog, and direct-by-name
dispatch.

Public API:
  - `await hub.topk(query, k, always_on=[...])`  → subset for BaseAgent
  - `await hub.call(name, args)`                 → direct dispatch (parsed JSON)
  - `await hub.close()`                          → drop catalog

Tool selection is intentionally simple: `always_on` pinned first, then the
rest of the catalog in catalog order up to `k`. No embedding, no ranking —
the catalog is small (~17 tools), agents already declare their relevant
tools via `always_on`, and a deterministic subset is easier to reason about
than a semantic-similarity gradient.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from src import obs
from src.core.telemetry import span

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
PINFILE = ROOT / "config" / "tools.yaml"


def _load_allowlist() -> set[str] | None:
    """Return the set of allowed tool names from ``config/tools.yaml``,
    or ``None`` if the file is absent / unreadable (unpinned mode)."""
    if not PINFILE.exists():
        return None
    try:
        data = yaml.safe_load(PINFILE.read_text()) or {}
    except Exception as exc:
        logger.warning("tool allowlist unreadable (%s): %s", PINFILE, exc)
        return None
    return set(data.get("allowed", []))


def _enforce_allowlist(
    tools: list[BaseTool], allowed: set[str] | None
) -> list[BaseTool]:
    """Refuse to start when the MCP server returned tools not in the allowlist.

    Closes the tool-schema-poisoning gap: a compromised or misconfigured
    server can't sneak a new tool past us. Missing tools (allowlist references
    something the server didn't return) are a soft warning — likely a deploy
    skew, not an attack.
    """
    if allowed is None:
        logger.warning(
            "ToolHub: %s not found — running unpinned (tool poisoning unchecked)",
            PINFILE,
        )
        return tools
    server_names = {t.name for t in tools}
    unexpected = server_names - allowed
    missing = allowed - server_names
    if unexpected:
        raise RuntimeError(
            "MCP server returned tools not in config/tools.yaml allowlist "
            "(possible schema poisoning or unsynced deploy): "
            f"{sorted(unexpected)}"
        )
    if missing:
        logger.warning(
            "Allowlist references tools the server didn't return: %s",
            sorted(missing),
        )
    return tools


def _connection() -> dict[str, Any]:
    if url := os.environ.get("MCP_SSE_URL"):
        return {"transport": "sse", "url": url}
    return {"transport": "stdio", "command": sys.executable, "args": ["-m", "src.mcp.server"]}


def _parse(raw: Any) -> Any:
    """JSON-decode MCP tool output for direct callers; pass through otherwise."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    return json.loads(item.get("text", ""))
                except json.JSONDecodeError:
                    return item.get("text", "")
    return raw


class ToolHub:
    """Connection + catalog + direct dispatch."""

    def __init__(self) -> None:
        self._client = MultiServerMCPClient({"obs": _connection()})
        self._tools: list[BaseTool] = []
        self._by_name: dict[str, BaseTool] = {}

    async def setup(self) -> None:
        """Idempotent: connect, list tools, enforce allowlist."""
        if self._tools:
            return
        raw = await self._client.get_tools(server_name="obs")
        self._tools = _enforce_allowlist(raw, _load_allowlist())
        self._by_name = {t.name: t for t in self._tools}
        logger.info("ToolHub: %d tools loaded", len(self._tools))

    async def topk(
        self,
        query: str,
        k: int,
        always_on: list[str] | tuple[str, ...] = (),
    ) -> list[BaseTool]:
        """Return up to ``k`` tools — ``always_on`` pinned first, rest in catalog order.

        The ``query`` arg is kept in the signature for forward-compat with a
        semantic ranker; today it's unused. Selection is deterministic so
        agents see the same tool set across runs.
        """
        await self.setup()
        picked: list[BaseTool] = []
        seen: set[str] = set()
        for name in always_on:
            if name in self._by_name and name not in seen:
                picked.append(self._by_name[name])
                seen.add(name)
            elif name not in self._by_name:
                logger.warning("ToolHub: always_on tool %r not found", name)
        if len(picked) >= k:
            return picked[:k]
        for tool in self._tools:
            if tool.name in seen:
                continue
            picked.append(tool)
            seen.add(tool.name)
            if len(picked) >= k:
                break
        logger.debug("ToolHub.topk(k=%d) → %s", k, [t.name for t in picked])
        return picked

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Invoke a tool by name. Returns parsed JSON when possible."""
        await self.setup()
        tool = self._by_name.get(name)
        if tool is None:
            raise KeyError(f"Tool {name!r} not found (have: {list(self._by_name)})")
        with span(f"mcp.{name}", {"mcp.tool": name, "mcp.arg_count": len(args)}):
            t0 = time.perf_counter()
            raw = await tool.ainvoke(args)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result = _parse(raw)
        # Redact *before* measuring bytes so payload size reflects what the
        # LLM (and audit log) actually sees.
        result = await obs.redact(result)
        try:
            n_bytes = len(json.dumps(result, default=str))
        except (TypeError, ValueError):
            n_bytes = len(str(result))
        obs.record_mcp(tool=name, ms=elapsed_ms, n_bytes=n_bytes)
        return result

    async def close(self) -> None:
        self._tools = []
        self._by_name = {}

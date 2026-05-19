"""Shared async JSON loader for MCP tools.

All MCP tools read from `data/*.json` via `load_json()`. Uses aiofiles for
non-blocking I/O and `asyncio.to_thread` to push the (CPU-bound) JSON parse
off the event loop. Results cached in-memory per process — files are mock
data that don't change at runtime.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiofiles

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_cache: dict[str, Any] = {}
_lock = asyncio.Lock()


async def load_json(filename: str) -> Any:
    """Load a JSON file under data/. Cached after first read."""
    if filename in _cache:
        return _cache[filename]

    async with _lock:
        if filename in _cache:  # re-check after acquiring lock
            return _cache[filename]
        path = DATA_DIR / filename
        async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
            content = await f.read()
        data = await asyncio.to_thread(json.loads, content)
        _cache[filename] = data
        return data


def clear_cache() -> None:
    """Test helper — clear the in-memory cache."""
    _cache.clear()

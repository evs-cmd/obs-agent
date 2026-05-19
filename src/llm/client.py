from __future__ import annotations

import time
from typing import Any

import litellm
from litellm import acompletion

from src import obs
from src.core.settings import get_settings
from src.core.telemetry import span


litellm.drop_params = True


async def llm_complete(
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.3,
    tools: list[dict] | None = None,
    response_format: Any = None,
    stream: bool = False,
    **kwargs,
):
    settings = get_settings()

    params: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
        **kwargs,
    }
    if tools:
        params["tools"] = tools
    if response_format:
        params["response_format"] = response_format

    if settings.openai_api_key:
        params["api_key"] = settings.openai_api_key

    attrs = {
        "llm.model": model,
        "llm.temperature": temperature,
        "llm.message_count": len(messages),
        "llm.has_tools": bool(tools),
        "llm.structured_output": bool(response_format),
    }
    with span("llm.complete", attrs) as s:
        t0 = time.perf_counter()
        response = await acompletion(**params)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Usage is available only on non-stream responses; stream usage
        # arrives at end-of-stream and would need an aggregating wrapper.
        # Cost for streamed calls is currently uncounted — known gap.
        usage = getattr(response, "usage", None) if not stream else None

        if s is not None and usage:
            s.set_attribute("llm.prompt_tokens", getattr(usage, "prompt_tokens", 0))
            s.set_attribute("llm.completion_tokens", getattr(usage, "completion_tokens", 0))
            s.set_attribute("llm.total_tokens", getattr(usage, "total_tokens", 0))

        if usage:
            # No-op outside a request scope; BudgetExceeded propagates up
            # and surfaces as an SSE error frame from the routes layer.
            obs.record_llm(
                model=model,
                in_tok=getattr(usage, "prompt_tokens", 0) or 0,
                out_tok=getattr(usage, "completion_tokens", 0) or 0,
                ms=elapsed_ms,
            )
        return response

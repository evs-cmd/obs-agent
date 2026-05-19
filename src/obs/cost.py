"""Per-request LLM cost tracking + session budget cap.

Prices are best-effort and must be kept in sync with provider price lists.
Unknown models cost $0 silently — pair this with ``events.jsonl`` to spot
zero-cost rows that should have had a model in ``PRICES``.

Budget cap: when ``ctx.cost_usd`` exceeds ``settings.max_cost_usd``,
``record_llm`` raises ``BudgetExceeded``. The graph node bubbles it up and
the stream emits an SSE ``error`` frame. Set ``max_cost_usd=0`` to disable.
"""
from __future__ import annotations

from src.core.settings import get_settings
from src.obs.base import get

# $ per *million* tokens (input, output). Extend as new models are used.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":       (0.15,  0.60),
    "gpt-4o":            (2.50, 10.00),
    "gpt-4.1-mini":      (0.40,  1.60),
    "gpt-4.1":           (2.00,  8.00),
    "o3-mini":           (1.10,  4.40),
    "claude-haiku-4-5":  (1.00,  5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}


class BudgetExceeded(RuntimeError):
    """Raised by ``record_llm`` when the per-session cost cap is breached."""


def _price(model: str, in_tok: int, out_tok: int) -> float:
    pi, po = PRICES.get(model, (0.0, 0.0))
    return (in_tok * pi + out_tok * po) / 1_000_000


def record_llm(model: str, in_tok: int, out_tok: int, ms: float) -> None:
    """Append an LLM-call record to the current ctx and enforce the budget.

    No-op outside a request scope — unit tests that call ``llm_complete``
    directly without ``obs.start`` won't crash.
    """
    ctx = get()
    if ctx is None:
        return
    usd = _price(model, in_tok, out_tok)
    ctx.llm_calls.append(
        {
            "model": model,
            "in": in_tok,
            "out": out_tok,
            "ms": round(ms, 1),
            "usd": round(usd, 6),
        }
    )
    ctx.cost_usd = round(ctx.cost_usd + usd, 6)
    cap = get_settings().max_cost_usd
    if cap and ctx.cost_usd > cap:
        raise BudgetExceeded(
            f"session {ctx.session_id} over budget: "
            f"${ctx.cost_usd:.3f} > ${cap:.3f}"
        )

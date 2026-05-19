from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from src.llm.client import llm_complete
from src.llm.prompts import render
from src.core.settings import get_app_config
from src.graph.state import AgentState

logger = logging.getLogger(__name__)

VALID_ROUTES = ("traces", "logs", "metrics", "incident", "out_of_scope")
MAX_QUERY_LEN = 2000


class _RouteSchema(BaseModel):
    route: Literal["traces", "logs", "metrics", "incident", "out_of_scope"] = Field(description="The specialized agent (or out_of_scope) to route this query to")
    reasoning: str = Field(description="Brief explanation of why this route was chosen")
    confidence: float = Field(ge=0, le=1, description="Confidence in the routing decision")


@dataclass
class RouteDecision:
    route: str
    reasoning: str
    confidence: float


async def _classify(user_query: str, model: str) -> _RouteSchema:
    response = await llm_complete(
        messages=[
            {"role": "system", "content": render("router_system")},
            {"role": "user", "content": user_query},
        ],
        model=model,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return _RouteSchema.model_validate_json(response.choices[0].message.content)


async def router_node(state: AgentState) -> dict:
    config = get_app_config()
    router_cfg = config.get("router", {})
    fallback = router_cfg.get("fallback_route", "incident")
    threshold = router_cfg.get("confidence_threshold", 0.5)
    max_retries = router_cfg.get("max_retries", 2)
    model = config["llm"]["router_model"]

    user_query = state["user_query"][:MAX_QUERY_LEN]
    parsed = None

    for attempt in range(max_retries + 1):
        try:
            parsed = await _classify(user_query, model)
            break
        except Exception as e:
            logger.warning(f"Router attempt {attempt + 1} failed: {e}")
            if attempt == max_retries:
                logger.error(f"Router exhausted retries, falling back to {fallback}")
                parsed = _RouteSchema(route=fallback, reasoning="Fallback after retry exhaustion", confidence=0.0)

    if parsed.route not in VALID_ROUTES:
        logger.warning(f"Invalid route '{parsed.route}', falling back to {fallback}")
        parsed.route = fallback

    # Defense-in-depth: even when the router picks an in-scope route, treat
    # very-low confidence as out-of-scope rather than running an agent on a
    # query the router barely understood. The router prompt already trains
    # `out_of_scope` directly; this is the safety net for miscalibration.
    if parsed.confidence < threshold:
        logger.info(
            f"Low confidence ({parsed.confidence:.2f} < {threshold}), "
            f"escalating to {fallback}"
        )
        parsed.route = fallback

    decision = RouteDecision(route=parsed.route, reasoning=parsed.reasoning, confidence=parsed.confidence)
    logger.info(f"Router: '{user_query[:80]}' → {decision.route} (confidence={decision.confidence:.2f}, {decision.reasoning})")

    return {
        "route": decision.route,
        "route_reasoning": decision.reasoning,
    }


def route_to_agent(state: AgentState) -> str:
    return state["route"]

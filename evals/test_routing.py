"""Router accuracy eval — exact-match classification + confidence threshold.

Cheap (~one mini-model call per case). Run with: `make eval-quick`.
"""
from __future__ import annotations

import pytest

from evals.conftest import all_cases, case_id
from src.core.settings import get_app_config
from src.graph.router import _classify


@pytest.mark.parametrize("case", all_cases(), ids=case_id)
async def test_router_classification(case: dict):
    config = get_app_config()
    model = config["llm"]["router_model"]

    parsed = await _classify(case["query"], model)

    assert parsed.route == case["expected_route"], (
        f"[{case['id']}] expected route={case['expected_route']!r}, "
        f"got {parsed.route!r} (confidence={parsed.confidence:.2f}, reasoning={parsed.reasoning!r})"
    )

    min_conf = case.get("min_confidence", 0.5)
    assert parsed.confidence >= min_conf, (
        f"[{case['id']}] confidence {parsed.confidence:.2f} below threshold {min_conf}"
    )

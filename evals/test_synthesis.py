"""Synthesis quality eval using deepeval.

Expensive (~7 LLM calls per case: 1 agent invocation + ~3 judge calls + retries).
Run with: `make eval`. Skipped by `make eval-quick`.

What it measures:
- Correctness  (GEval): does the analysis identify the real root cause?
- Actionability (GEval): is the recommendation immediately executable?
- Faithfulness (FaithfulnessMetric): are claims grounded in the provided context?

Note: our `src/llm/validation.py` does *exact-string* citation checking (complementary).
deepeval's HallucinationMetric/FaithfulnessMetric does semantic checking. Both run.
"""
from __future__ import annotations

import json

import pytest

deepeval = pytest.importorskip(
    "deepeval",
    reason="deepeval not installed — `pip install -e \".[dev]\"` to enable synthesis evals",
)

from deepeval import assert_test  # noqa: E402
from deepeval.metrics import FaithfulnessMetric, GEval  # noqa: E402
from deepeval.test_case import LLMTestCase, LLMTestCaseParams  # noqa: E402

from evals.conftest import case_id, synth_cases  # noqa: E402
from src.mcp.tools.correlation import get_incident_context  # noqa: E402

JUDGE_MODEL = "gpt-4o-mini"


def _correctness_metric() -> GEval:
    return GEval(
        name="Correctness",
        criteria=(
            "Determine whether the incident analysis correctly identifies the root cause "
            "based on the provided context (logs, traces, metrics, errors). "
            "A correct analysis names the underlying issue, not just symptoms."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.RETRIEVAL_CONTEXT,
        ],
        threshold=0.6,
        model=JUDGE_MODEL,
    )


def _actionability_metric() -> GEval:
    return GEval(
        name="Actionability",
        criteria=(
            "Is the recommendation concrete, specific, and immediately executable by an "
            "on-call engineer? Generic advice scores low; specific commands or steps score high."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
        threshold=0.6,
        model=JUDGE_MODEL,
    )


@pytest.mark.expensive
@pytest.mark.parametrize("case", synth_cases(), ids=case_id)
async def test_incident_synthesis_quality(case: dict, agents, hub):
    """Run the incident agent end-to-end, evaluate the synthesis with deepeval metrics."""
    agent = agents["incident"]

    synthesis = await agent.handle(case["query"], hub)
    assert synthesis, f"[{case['id']}] empty synthesis"

    # Build retrieval_context from the same correlation source the agent saw
    service = case.get("expected_services", ["payment-service"])[0]
    context = get_incident_context(service=service)
    context_str = json.dumps(context, default=str)

    test_case = LLMTestCase(
        input=case["query"],
        actual_output=synthesis,
        retrieval_context=[context_str],
    )

    assert_test(test_case, [
        _correctness_metric(),
        _actionability_metric(),
        FaithfulnessMetric(threshold=0.7, model=JUDGE_MODEL),
    ])


@pytest.mark.expensive
@pytest.mark.parametrize("case", synth_cases(), ids=case_id)
async def test_incident_synthesis_keywords(case: dict, agents, hub):
    """Surface-level checks: expected keywords + services appear in the synthesis."""
    agent = agents["incident"]
    synthesis = await agent.handle(case["query"], hub)

    text_low = synthesis.lower()
    for kw in case.get("expected_keywords", []):
        assert kw.lower() in text_low, (
            f"[{case['id']}] missing keyword {kw!r} in synthesis"
        )

    for svc in case.get("expected_services", []):
        assert svc in synthesis, (
            f"[{case['id']}] missing service {svc!r} in synthesis"
        )

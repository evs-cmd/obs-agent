"""Tests for the self-critique flow.

Unit tests only — they exercise the routing logic and schema, not the LLM call.
End-to-end critique behavior is covered by the eval suite.
"""
from src.llm.schemas import CritiqueVerdict


def test_critique_verdict_validates():
    v = CritiqueVerdict(
        assessment="partially_supported",
        issues=["Root cause claim doesn't match the cited trace span"],
        revised_confidence=0.5,
        verdict="return_with_caveats",
    )
    assert v.assessment == "partially_supported"
    assert v.revised_confidence == 0.5


def test_critique_to_markdown_includes_issues():
    v = CritiqueVerdict(
        assessment="speculative",
        issues=["Issue A", "Issue B"],
        revised_confidence=0.3,
        verdict="needs_human_review",
    )
    md = v.to_markdown()
    assert "Self-Critique" in md
    assert "Issue A" in md
    assert "Issue B" in md
    assert "30%" in md
    assert "needs human review" in md


def test_critique_to_markdown_no_issues():
    v = CritiqueVerdict(
        assessment="well_supported",
        issues=[],
        revised_confidence=0.9,
        verdict="return_as_is",
    )
    md = v.to_markdown()
    assert "No specific issues noted" in md


def test_should_critique_triggers_below_threshold():
    """Test the routing function in isolation by constructing a minimal agent."""
    from src.agents.incident import IncidentAgent, IncidentState

    agent = IncidentAgent.__new__(IncidentAgent)
    agent.enable_self_critique = True
    agent.self_critique_threshold = 0.7
    agent.max_critique_passes = 1

    def st(synthesis, critique_iteration=0):
        return IncidentState(query="x", synthesis=synthesis, critique_iteration=critique_iteration)

    assert agent._should_critique(st({"confidence": 0.5})) == "critique"
    assert agent._should_critique(st({"confidence": 0.9})) == "end"
    assert agent._should_critique(st({})) == "end"
    # capped: once we've already critiqued max_critique_passes times, stop
    assert agent._should_critique(st({"confidence": 0.5}, critique_iteration=1)) == "end"

    agent.enable_self_critique = False
    assert agent._should_critique(st({"confidence": 0.1})) == "end"

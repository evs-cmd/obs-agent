"""Pydantic schemas for LLM trust boundaries.

Every place where an LLM produces structured data should validate against a schema here.
This gives us type safety, structured output, and a foundation for hallucination detection.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """A single piece of evidence cited in the synthesis. Must reference real data from context."""
    type: Literal["trace_id", "log_id", "error_id", "metric", "service"] = Field(
        description="What kind of reference this is"
    )
    ref: str = Field(description="The actual ID or name being cited")
    note: str = Field(description="Why this evidence is relevant")


class IncidentSynthesis(BaseModel):
    """Structured output for incident analysis. The LLM must produce this shape."""
    impact: str = Field(description="What is broken and severity (1-2 sentences)")
    root_cause: str = Field(description="Most likely underlying issue (1-2 sentences)")
    evidence: list[Evidence] = Field(description="Specific data points supporting the conclusion")
    recommendation: str = Field(description="Immediate mitigation steps")
    follow_up: str = Field(description="What to monitor after mitigation")
    confidence: float = Field(ge=0, le=1, description="Confidence in the analysis")

    def to_markdown(self) -> str:
        """Render as the user-facing markdown format."""
        evidence_lines = [f"- **{e.type}** `{e.ref}`: {e.note}" for e in self.evidence]
        evidence_block = "\n".join(evidence_lines) if evidence_lines else "_No specific evidence cited._"

        return (
            f"## Impact\n{self.impact}\n\n"
            f"## Root Cause\n{self.root_cause}\n\n"
            f"## Evidence\n{evidence_block}\n\n"
            f"## Recommendation\n{self.recommendation}\n\n"
            f"## Follow-up\n{self.follow_up}\n\n"
            f"_Confidence: {self.confidence:.0%}_"
        )


class CritiqueVerdict(BaseModel):
    """Self-critique of an incident synthesis.

    Produced by a second LLM call when the original synthesis confidence is
    below threshold. Lets the system surface its own uncertainty rather than
    presenting a low-confidence answer as authoritative.
    """
    assessment: Literal["well_supported", "partially_supported", "speculative"] = Field(
        description="How well does the evidence back the claims?"
    )
    issues: list[str] = Field(
        description="Specific weaknesses found in the analysis (cite which claim is weak and why)"
    )
    revised_confidence: float = Field(
        ge=0, le=1,
        description="Adjusted confidence after critique",
    )
    verdict: Literal["return_as_is", "return_with_caveats", "needs_human_review"] = Field(
        description="What should happen with this analysis?"
    )

    def to_markdown(self) -> str:
        issue_lines = "\n".join(f"- {issue}" for issue in self.issues) if self.issues else "_No specific issues noted._"
        return (
            "\n\n---\n\n"
            f"## Self-Critique\n"
            f"**Assessment:** {self.assessment.replace('_', ' ')}  \n"
            f"**Revised confidence:** {self.revised_confidence:.0%}  \n"
            f"**Verdict:** {self.verdict.replace('_', ' ')}\n\n"
            f"**Issues identified:**\n{issue_lines}"
        )

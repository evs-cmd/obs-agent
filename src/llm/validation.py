"""Citation validation — detects hallucinated references in LLM output.

When an LLM cites a trace_id or log_id, we verify it actually exists in the context
that was provided. Invalid citations are flagged or stripped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.llm.schemas import Evidence, IncidentSynthesis

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    valid_citations: list[Evidence]
    invalid_citations: list[Evidence]
    hallucination_rate: float

    @property
    def is_clean(self) -> bool:
        return not self.invalid_citations


def _extract_refs(context: dict[str, Any]) -> dict[str, set[str]]:
    """Build a set of valid references per type, from the correlated context."""
    refs: dict[str, set[str]] = {
        "trace_id": set(),
        "log_id": set(),
        "error_id": set(),
        "service": set(),
        "metric": set(),
    }

    for trace in context.get("traces", []):
        if tid := trace.get("trace_id"):
            refs["trace_id"].add(tid)
        for span in trace.get("spans", []):
            if svc := span.get("service"):
                refs["service"].add(svc)

    for log in context.get("logs", []):
        if lid := log.get("id"):
            refs["log_id"].add(lid)
        if svc := log.get("service"):
            refs["service"].add(svc)
        if tid := log.get("trace_id"):
            refs["trace_id"].add(tid)

    for err in context.get("errors", []):
        if eid := err.get("error_id"):
            refs["error_id"].add(eid)
        if svc := err.get("service"):
            refs["service"].add(svc)
        if tid := err.get("trace_id"):
            refs["trace_id"].add(tid)

    metrics = context.get("metrics", {})
    if isinstance(metrics, dict):
        for svc, rows in metrics.items():
            refs["service"].add(svc)
            if isinstance(rows, list):
                for m in rows:
                    if name := m.get("metric"):
                        refs["metric"].add(name)

    for svc in context.get("services", []):
        refs["service"].add(svc)

    return refs


def validate_citations(synthesis: IncidentSynthesis, context: dict[str, Any]) -> ValidationReport:
    """Verify every cited reference in synthesis exists in the provided context."""
    valid_refs = _extract_refs(context)

    valid: list[Evidence] = []
    invalid: list[Evidence] = []

    for ev in synthesis.evidence:
        allowed = valid_refs.get(ev.type, set())
        if ev.ref in allowed:
            valid.append(ev)
        else:
            invalid.append(ev)
            logger.warning(
                "Hallucinated citation: type=%s ref=%s not in context (had %d valid)",
                ev.type, ev.ref, len(allowed),
            )

    total = len(synthesis.evidence)
    rate = (len(invalid) / total) if total > 0 else 0.0

    return ValidationReport(
        valid_citations=valid,
        invalid_citations=invalid,
        hallucination_rate=rate,
    )


def strip_hallucinations(synthesis: IncidentSynthesis, report: ValidationReport) -> IncidentSynthesis:
    """Return a new synthesis with hallucinated citations removed."""
    if report.is_clean:
        return synthesis
    return synthesis.model_copy(update={"evidence": report.valid_citations})

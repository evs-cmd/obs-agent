from src.llm.schemas import Evidence, IncidentSynthesis
from src.llm.validation import strip_hallucinations, validate_citations


def _synthesis_with_evidence(refs: list[tuple[str, str]]) -> IncidentSynthesis:
    return IncidentSynthesis(
        impact="test",
        root_cause="test",
        evidence=[Evidence(type=t, ref=r, note="n") for t, r in refs],
        recommendation="test",
        follow_up="test",
        confidence=0.8,
    )


def test_validate_all_valid():
    ctx = {
        "traces": [{"trace_id": "t-abc-002", "spans": [{"service": "payment-service"}]}],
        "logs": [{"id": "log-012", "service": "payment-service", "trace_id": "t-abc-002"}],
        "errors": [{"error_id": "err-001", "service": "payment-service"}],
        "metrics": {"payment-service": [{"metric": "error_rate"}]},
        "services": ["payment-service"],
    }
    synth = _synthesis_with_evidence([
        ("trace_id", "t-abc-002"),
        ("log_id", "log-012"),
        ("error_id", "err-001"),
        ("service", "payment-service"),
        ("metric", "error_rate"),
    ])
    report = validate_citations(synth, ctx)
    assert report.is_clean
    assert report.hallucination_rate == 0.0
    assert len(report.valid_citations) == 5


def test_validate_detects_hallucinated_trace():
    ctx = {"traces": [{"trace_id": "t-real-001", "spans": []}]}
    synth = _synthesis_with_evidence([("trace_id", "t-fake-999")])
    report = validate_citations(synth, ctx)
    assert not report.is_clean
    assert len(report.invalid_citations) == 1
    assert report.hallucination_rate == 1.0


def test_validate_partial_hallucination():
    ctx = {"traces": [{"trace_id": "t-real-001", "spans": []}]}
    synth = _synthesis_with_evidence([
        ("trace_id", "t-real-001"),
        ("trace_id", "t-fake-999"),
    ])
    report = validate_citations(synth, ctx)
    assert len(report.valid_citations) == 1
    assert len(report.invalid_citations) == 1
    assert report.hallucination_rate == 0.5


def test_strip_hallucinations():
    ctx = {"traces": [{"trace_id": "t-real-001", "spans": []}]}
    synth = _synthesis_with_evidence([
        ("trace_id", "t-real-001"),
        ("trace_id", "t-fake-999"),
    ])
    report = validate_citations(synth, ctx)
    cleaned = strip_hallucinations(synth, report)
    assert len(cleaned.evidence) == 1
    assert cleaned.evidence[0].ref == "t-real-001"


def test_synthesis_to_markdown():
    synth = _synthesis_with_evidence([("trace_id", "t-abc-002")])
    md = synth.to_markdown()
    assert "## Impact" in md
    assert "## Root Cause" in md
    assert "t-abc-002" in md
    assert "80%" in md

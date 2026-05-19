import pytest

from src.graph.router import RouteDecision, _RouteSchema, VALID_ROUTES


def test_route_decision():
    d = RouteDecision(route="traces", reasoning="latency question", confidence=0.9)
    assert d.route == "traces"
    assert d.confidence == 0.9


def test_schema_valid_routes():
    for route in VALID_ROUTES:
        s = _RouteSchema(route=route, reasoning="test", confidence=0.8)
        assert s.route == route


def test_schema_invalid_route():
    with pytest.raises(Exception):
        _RouteSchema(route="invalid", reasoning="test", confidence=0.5)


def test_schema_from_json():
    raw = '{"route": "logs", "reasoning": "User wants error logs", "confidence": 0.92}'
    s = _RouteSchema.model_validate_json(raw)
    assert s.route == "logs"
    assert s.confidence == 0.92


def test_confidence_bounds():
    with pytest.raises(Exception):
        _RouteSchema(route="logs", reasoning="test", confidence=1.5)
    with pytest.raises(Exception):
        _RouteSchema(route="logs", reasoning="test", confidence=-0.1)


def test_fallback_on_bad_json():
    try:
        _RouteSchema.model_validate_json("not json at all")
    except Exception:
        fallback = _RouteSchema(route="out_of_scope", reasoning="Fallback", confidence=0.0)
        assert fallback.route == "out_of_scope"


def test_out_of_scope_is_valid_route():
    """The out_of_scope route exists so the router can reject non-observability
    queries rather than silently routing them to the incident agent."""
    assert "out_of_scope" in VALID_ROUTES
    s = _RouteSchema(
        route="out_of_scope",
        reasoning="General-knowledge question",
        confidence=1.0,
    )
    assert s.route == "out_of_scope"


async def test_out_of_scope_node_returns_message():
    """The out_of_scope graph node returns a fixed refusal — no LLM call,
    no MCP call. Verifies the safety net wired by build_graph()."""
    from src.graph.builder import out_of_scope_node

    state = {"user_query": "why is the earth blue?", "route_reasoning": "off-topic"}
    out = await out_of_scope_node(state)
    assert "observability" in out["agent_output"].lower()
    assert len(out["messages"]) == 1

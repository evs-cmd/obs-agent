import pytest

from src.agents.base import BaseAgent
from src.graph.registry import load_agents
from src.core.settings import get_app_config


def test_config_has_agents():
    config = get_app_config()
    assert "agents" in config
    assert len(config["agents"]) == 4


def test_all_agents_load():
    agents = load_agents()
    assert "traces" in agents
    assert "logs" in agents
    assert "metrics" in agents
    assert "incident" in agents


def test_agents_are_base_agent():
    agents = load_agents()
    for name, agent in agents.items():
        assert isinstance(agent, BaseAgent), f"{name} is not a BaseAgent"


def test_agents_have_config():
    agents = load_agents()
    for name, agent in agents.items():
        assert agent.model, f"{name} missing model"
        assert agent.system_prompt, f"{name} missing system_prompt"
        assert agent.description, f"{name} missing description"


def test_disabled_agent_excluded():
    config = get_app_config()
    config["agents"]["traces"]["enabled"] = False
    from src.graph import registry
    agents = registry.load_agents()
    assert "traces" not in agents
    config["agents"]["traces"]["enabled"] = True

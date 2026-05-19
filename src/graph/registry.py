from __future__ import annotations

from src.agents.base import BaseAgent
from src.agents.traces import TracesAgent
from src.agents.logs import LogsAgent
from src.agents.metrics import MetricsAgent
from src.agents.incident import IncidentAgent
from src.core.settings import get_app_config


AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "TracesAgent": TracesAgent,
    "LogsAgent": LogsAgent,
    "MetricsAgent": MetricsAgent,
    "IncidentAgent": IncidentAgent,
}


def load_agents() -> dict[str, BaseAgent]:
    config = get_app_config()
    agents = {}
    for name, cfg in config["agents"].items():
        if not cfg.get("enabled", True):
            continue
        cls = AGENT_REGISTRY[cfg["class"]]
        agents[name] = cls(cfg)
    return agents

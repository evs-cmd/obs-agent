from src.agents.base import BaseAgent


class TracesAgent(BaseAgent):
    """Trace queries — inherits ReAct; tools pinned via always_on_tools in config."""

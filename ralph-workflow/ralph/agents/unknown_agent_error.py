"""Errors raised when agent lookup fails."""

from ralph.agents.registry import agy_alias_help


class UnknownAgentError(Exception):
    """Raised when an agent name is not found in the registry.

    Attributes:
        agent_name: The unknown agent name.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        msg = f"Unknown agent: '{agent_name}'. Register the agent in the configuration."
        if agent_name.startswith("agy/"):
            msg = f"{msg} {agy_alias_help()}"
        super().__init__(msg)

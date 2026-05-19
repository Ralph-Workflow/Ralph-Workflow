"""Errors raised when agent lookup fails."""


class UnknownAgentError(Exception):
    """Raised when an agent name is not found in the registry.

    Attributes:
        agent_name: The unknown agent name.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        msg = f"Unknown agent: '{agent_name}'. Register the agent in the configuration."
        super().__init__(msg)

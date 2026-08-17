"""Errors raised when agent lookup fails."""

from ralph.agents.registry import lookup_dynamic_alias_help


class UnknownAgentError(Exception):
    """Raised when an agent name is not found in the registry.

    Attributes:
        agent_name: The unknown agent name.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        msg = f"Unknown agent: '{agent_name}'. Register the agent in the configuration."
        # Two-phase data-driven lookup (catalog exact match, then the
        # registered-prefix fallback table): any agent that registers
        # ``dynamic_alias_help`` gets the same hint here — no name-typed
        # branch in this path.
        alias_help = lookup_dynamic_alias_help(agent_name)
        if alias_help is not None:
            msg = f"{msg} {alias_help}"
        super().__init__(msg)

"""Typed failure for an agent invocation missing provider credentials."""

from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class MissingCredentialsError(AgentInvocationError):
    """Signal that recovery must fall over because a required credential is absent."""

    def __init__(self, agent_name: str, detail: str) -> None:
        self.skip_same_agent_retries = True
        self.env_var = detail.split(maxsplit=1)[0]
        super().__init__(agent_name, 1, detail)


__all__ = ["MissingCredentialsError"]

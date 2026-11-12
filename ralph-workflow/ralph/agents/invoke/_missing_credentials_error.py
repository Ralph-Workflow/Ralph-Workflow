from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class MissingCredentialsError(AgentInvocationError):
    def __init__(self, agent_name: str, detail: str) -> None:
        self.skip_same_agent_retries = True
        self.env_var = detail.split(maxsplit=1)[0]
        super().__init__(agent_name, 1, detail)


__all__ = ["MissingCredentialsError"]

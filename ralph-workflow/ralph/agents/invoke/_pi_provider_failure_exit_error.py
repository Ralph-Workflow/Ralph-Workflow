
from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class PiProviderFailureExitError(AgentInvocationError):

    def __init__(self, agent_name: str, reason: str) -> None:
        self.skip_same_agent_retries = True
        super().__init__(
            agent_name,
            0,
            f"pi agent provider failure (stopReason=error): {reason}",
        )


__all__ = ["PiProviderFailureExitError"]

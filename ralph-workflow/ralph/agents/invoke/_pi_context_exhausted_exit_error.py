from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class PiContextExhaustedExitError(AgentInvocationError):
    __slots__ = ("skip_same_agent_retries",)

    def __init__(self, agent_name: str) -> None:
        self.skip_same_agent_retries = True
        super().__init__(
            agent_name,
            0,
            ("pi agent context length exhausted (stopReason=length; advance to the next agent)"),
        )


__all__ = ["PiContextExhaustedExitError"]

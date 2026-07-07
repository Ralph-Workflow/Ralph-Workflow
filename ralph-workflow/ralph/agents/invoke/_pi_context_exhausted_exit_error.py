"""Pi-specific clean-exit error for context length exhaustion."""

from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class PiContextExhaustedExitError(AgentInvocationError):
    """Raised when Pi exits cleanly after a length-limited model turn."""

    def __init__(self, agent_name: str) -> None:
        self.skip_same_agent_retries = True
        super().__init__(
            agent_name,
            0,
            (
                "pi agent context length exhausted "
                "(stopReason=length; advance to the next agent)"
            ),
        )


__all__ = ["PiContextExhaustedExitError"]

"""Pi-specific clean-exit error for an unreachable provider/model."""

from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class PiProviderFailureExitError(AgentInvocationError):
    """Raised when Pi exits rc=0 after its retry ladder failed outright.

    Pi reports an unreachable provider on the message object
    (``stopReason='error'`` plus ``errorMessage``) and then exhausts
    its own bounded retry ladder before exiting **cleanly**. Without
    this error the completion gate reported only "no completion
    evidence", which is a *resumable* verdict -- so Ralph relaunched
    the same session against the same dead provider indefinitely.

    ``skip_same_agent_retries`` is set because retrying the same
    agent is futile while its provider is down: the runner should
    fall over to the next agent instead.
    """

    def __init__(self, agent_name: str, reason: str) -> None:
        self.skip_same_agent_retries = True
        super().__init__(
            agent_name,
            0,
            f"pi agent provider failure (stopReason=error): {reason}",
        )


__all__ = ["PiProviderFailureExitError"]

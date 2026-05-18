"""OpenCodeResumableExitError — raised when an agent exits without completion evidence."""

from __future__ import annotations

from ralph.agents.invoke._errors import AgentInvocationError


class OpenCodeResumableExitError(AgentInvocationError):
    """Raised when an agent session exits without required completion evidence.

    The session can be continued; the runner maps this into a session-preserving retry.
    """

    def __init__(self, agent_name: str, session_id: str | None = None) -> None:
        self.resumable_session_id = session_id
        super().__init__(
            agent_name,
            0,
            (
                "agent session exited without required completion evidence "
                "(no artifact, no declare_complete)"
            ),
        )


__all__ = ["OpenCodeResumableExitError"]

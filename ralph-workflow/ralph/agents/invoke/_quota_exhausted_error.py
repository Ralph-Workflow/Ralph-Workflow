"""Typed terminal error for an agent whose quota or rate limit is exhausted."""

from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class QuotaExhaustedError(AgentInvocationError):
    """Raised when an agent cannot continue until its quota or rate limit resets."""

    def __init__(self, agent_name: str) -> None:
        self.skip_same_agent_retries = True
        super().__init__(agent_name, 0, "quota or rate limit is exhausted")


__all__ = ["QuotaExhaustedError"]

"""Typed terminal error for an agent whose quota or rate limit is exhausted."""

from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError

_MAX_QUOTA_DETAIL_CHARS = 512


class QuotaExhaustedError(AgentInvocationError):
    """Raised when an agent cannot continue until its quota or rate limit resets."""

    def __init__(self, agent_name: str, detail: str | None = None) -> None:
        self.skip_same_agent_retries = True
        excerpt = ""
        if detail:
            excerpt = "".join(
                character if character.isprintable() else " "
                for character in detail[:_MAX_QUOTA_DETAIL_CHARS]
            ).strip()
        message = "quota or rate limit is exhausted"
        if excerpt:
            message = f"{excerpt}; {message}"
        super().__init__(agent_name, 0, message)


__all__ = ["QuotaExhaustedError"]

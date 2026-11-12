from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.recovery.attempt_facts import TerminationIssuer

_MAX_QUOTA_DETAIL_CHARS = 512


class QuotaExhaustedError(AgentInvocationError):
    def __init__(
        self,
        agent_name: str,
        detail: str | None = None,
        *,
        returncode: int = 0,
        issuer: str = TerminationIssuer.QUOTA_DETECTOR.value,
    ) -> None:
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
        super().__init__(agent_name, returncode, message, issuer=issuer)


__all__ = ["QuotaExhaustedError"]

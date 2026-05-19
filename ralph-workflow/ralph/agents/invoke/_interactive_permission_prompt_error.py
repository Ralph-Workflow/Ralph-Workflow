"""InteractivePermissionPromptError — raised when interactive Claude hits a permission prompt."""

from __future__ import annotations

from ralph.agents.invoke._errors import AgentInvocationError


class InteractivePermissionPromptError(AgentInvocationError):
    """Raised when interactive Claude reaches a permission prompt in unattended mode."""

    def __init__(self, agent_name: str, parsed_output: list[str]) -> None:
        super().__init__(
            agent_name,
            -1,
            "Interactive Claude reached a permission prompt in unattended mode",
            parsed_output,
        )


__all__ = ["InteractivePermissionPromptError"]

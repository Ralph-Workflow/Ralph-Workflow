from __future__ import annotations

from ralph.agents.invoke._errors import AgentInvocationError


class InteractivePermissionPromptError(AgentInvocationError):
    def __init__(self, agent_name: str, parsed_output: list[str]) -> None:
        super().__init__(
            agent_name,
            -1,
            "Interactive Claude reached a permission prompt in unattended mode",
            parsed_output,
        )


__all__ = ["InteractivePermissionPromptError"]

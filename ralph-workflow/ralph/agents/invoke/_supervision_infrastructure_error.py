"""Typed failure when conflict-resolution liveness infrastructure is unavailable."""

from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError


class SupervisionInfrastructureError(AgentInvocationError):
    """The required activity relay failed before an inactivity verdict was allowed."""

    def __init__(self, agent_name: str, detail: str) -> None:
        self.detail = detail
        super().__init__(
            agent_name,
            -1,
            f"SUPERVISION_INFRASTRUCTURE_FAILURE: {detail}",
            [],
        )


__all__ = ["SupervisionInfrastructureError"]

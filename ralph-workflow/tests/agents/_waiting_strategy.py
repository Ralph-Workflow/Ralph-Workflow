from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState, GenericExecutionStrategy


class _WaitingStrategy(GenericExecutionStrategy):
    """Strategy whose classify_quiet always returns WAITING_ON_CHILD."""

    def classify_quiet(
        self,
        handle: object,
        liveness_probe: object,
    ) -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

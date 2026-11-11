from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState, GenericExecutionStrategy


class _WaitingStrategy(GenericExecutionStrategy):
    def classify_quiet(
        self,
        handle: object,
        liveness_probe: object,
    ) -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

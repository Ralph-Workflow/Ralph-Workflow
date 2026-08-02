from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState, GenericExecutionStrategy


class _RaisingStrategy(GenericExecutionStrategy):
    """Strategy whose classify_quiet always raises to simulate a transient probe failure."""

    def classify_quiet(
        self,
        handle: object,
        liveness_probe: object,
    ) -> AgentExecutionState:
        raise RuntimeError("boom")

"""Execution strategy for Google Anti Gravity (AGY) agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import _check_signals_terminal
from .agent_execution_state import AgentExecutionState
from .generic_execution_strategy import GenericExecutionStrategy

if TYPE_CHECKING:
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


class AgyExecutionStrategy(GenericExecutionStrategy):
    """AGY strategy: completion evidence still required."""

    def classify_exit(
        self,
        handle: _LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, liveness_probe
        if _check_signals_terminal(completion_signals):
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.RESUMABLE_CONTINUE

    def supports_completion_enforcement(self) -> bool:
        return True

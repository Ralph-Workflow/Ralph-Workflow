from __future__ import annotations

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestExplicitCompletionSucceeds:
    def test_explicit_completion_signal_succeeds(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        signals = CompletionSignals(
            explicit_complete=True,
            required_artifact_present=True,
            artifact_types=("development_result",),
            completion_sentinel_present=True,
            artifact_required=True,
        )

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

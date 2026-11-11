from __future__ import annotations

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestForegroundExitWithoutCompletion:
    def test_foreground_exit_without_explicit_completion_is_not_terminal(
        self,
    ) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals)

        assert state != AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected non-terminal state when no completion signals; got {state!r}"
        )

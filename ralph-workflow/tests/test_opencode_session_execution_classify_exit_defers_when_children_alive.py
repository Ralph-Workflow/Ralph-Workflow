from __future__ import annotations

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestClassifyExitDefersWhenChildrenAlive:
    def test_classify_exit_returns_waiting_when_liveness_probe_active(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)
        probe = FakeLivenessProbe(active=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_returns_waiting_when_handle_has_descendants(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=False)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_completion_signals_take_precedence_over_live_children(
        self,
    ) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=True)
        signals = CompletionSignals(
            True,
            True,
            ("development_result",),
            completion_sentinel_present=True,
            artifact_required=True,
        )

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

    def test_classify_exit_with_no_probe_falls_back_to_descendant_check(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.WAITING_ON_CHILD

"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations
from tests.fake_handle import _FakeHandle

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)
from ralph.process.liveness import FakeLivenessProbe

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestClassifyExitDefersWhenChildrenAlive:
    """classify_exit must return WAITING_ON_CHILD when children are alive."""



    def test_classify_exit_returns_waiting_when_liveness_probe_active(self) -> None:
        """Liveness probe reporting active agents → WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)
        probe = FakeLivenessProbe(active=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_returns_waiting_when_handle_has_descendants(self) -> None:
        """handle.has_live_descendants() True → WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=False)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_completion_signals_take_precedence_over_live_children(
        self,
    ) -> None:
        """Strong completion signals → TERMINAL_COMPLETE even with live children."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=True)
        signals = CompletionSignals(True, True, ("development_result",))

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

    def test_classify_exit_with_no_probe_falls_back_to_descendant_check(self) -> None:
        """No liveness_probe → fallback to handle.has_live_descendants() for WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.WAITING_ON_CHILD



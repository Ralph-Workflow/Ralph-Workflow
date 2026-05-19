"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
)
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestGenericExecutionStrategy:
    """GenericExecutionStrategy retains single-process exit-success semantics."""

    def test_classify_quiet_returns_active_when_no_descendants(self) -> None:
        """No descendants → classify_quiet returns ACTIVE (not WAITING_ON_CHILD)."""
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE

    def test_classify_quiet_returns_waiting_when_has_live_descendants(self) -> None:
        """OS-level live descendants → classify_quiet returns WAITING_ON_CHILD."""
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_quiet_does_not_use_liveness_probe(self) -> None:
        """Generic strategy must not escalate to WAITING_ON_CHILD via FakeLivenessProbe alone."""
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state != AgentExecutionState.WAITING_ON_CHILD, (
            "GenericExecutionStrategy must not honour the liveness probe; "
            f"got {state!r} purely because probe says active"
        )

    def test_classify_exit_always_returns_terminal_complete(self) -> None:
        """Exit is always TERMINAL_COMPLETE regardless of completion signals."""
        strategy = GenericExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        no_signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, no_signals)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

    def test_supports_session_continuation_is_false(self) -> None:
        """Generic agents do not support session continuation."""
        strategy = GenericExecutionStrategy()
        assert strategy.supports_session_continuation() is False

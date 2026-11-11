from __future__ import annotations

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestGenericExecutionStrategy:
    def test_classify_quiet_returns_active_when_no_descendants(self) -> None:
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE

    def test_classify_quiet_returns_waiting_when_has_live_descendants(self) -> None:
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_quiet_does_not_use_liveness_probe(self) -> None:
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state != AgentExecutionState.WAITING_ON_CHILD, (
            "GenericExecutionStrategy must not honour the liveness probe; "
            f"got {state!r} purely because probe says active"
        )

    def test_classify_exit_always_returns_terminal_complete(self) -> None:
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
        strategy = GenericExecutionStrategy()
        assert strategy.supports_session_continuation() is False

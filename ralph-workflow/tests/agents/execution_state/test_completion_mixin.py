"""Black-box tests for CompletionEnforcingStrategy mixin.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    CompletionEnforcingStrategy,
    GenericExecutionStrategy,
)
from tests.fake_handle import _FakeHandle


class _HostWithCompletionEnforcement(CompletionEnforcingStrategy, GenericExecutionStrategy):
    def supports_completion_enforcement(self) -> bool:
        return True


class TestCompletionEnforcingStrategy:
    """Mixin routes classify_exit through _check_signals_terminal."""

    def test_classify_exit_terminal_when_terminal_ack_seen(self) -> None:
        strategy = _HostWithCompletionEnforcement()
        handle = _FakeHandle(returncode=0)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
            terminal_ack_seen=True,
        )

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

    def test_classify_exit_resumable_when_no_terminal_signals(self) -> None:
        strategy = _HostWithCompletionEnforcement()
        handle = _FakeHandle(returncode=0)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.RESUMABLE_CONTINUE

    def test_mixin_requires_supports_completion_enforcement(self) -> None:
        with pytest.raises(TypeError):
            type("_MissingEnforcement", (CompletionEnforcingStrategy,), {})

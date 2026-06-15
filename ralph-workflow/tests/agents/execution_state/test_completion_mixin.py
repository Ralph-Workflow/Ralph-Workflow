"""Black-box tests for CompletionEnforcingStrategy mixin.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    BaseExecutionStrategy,
    CompletionEnforcingStrategy,
    GenericExecutionStrategy,
)
from tests.fake_handle import _FakeHandle


class _HostWithCompletionEnforcement(CompletionEnforcingStrategy, GenericExecutionStrategy):
    """Host that inherits True from the mixin."""


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

    def test_mixin_allows_host_that_inherits_enforcement(self) -> None:
        """A host inheriting the mixin's True default is accepted."""
        cls = type(
            "_EnforcementHost",
            (CompletionEnforcingStrategy, GenericExecutionStrategy),
            dict[str, object](),
        )
        assert cls().supports_completion_enforcement() is True

    def test_mixin_rejects_host_that_overrides_enforcement(self) -> None:
        """A host that shadows the mixin's capability method is rejected."""
        with pytest.raises(TypeError):
            type(
                "_OverriddenEnforcementHost",
                (CompletionEnforcingStrategy, GenericExecutionStrategy),
                {"supports_completion_enforcement": lambda self: True},
            )

    def test_mixin_rejects_host_returning_false(self) -> None:
        """A subclass explicitly returning False cannot bypass enforcement."""
        with pytest.raises(TypeError):
            type(
                "_FalseEnforcementHost",
                (CompletionEnforcingStrategy, GenericExecutionStrategy),
                {"supports_completion_enforcement": lambda self: False},
            )

    def test_intended_concrete_host_reports_enforcement_enabled(self) -> None:
        strategy = _HostWithCompletionEnforcement()
        assert strategy.supports_completion_enforcement() is True

    def test_non_enforcing_base_strategy_returns_false(self) -> None:
        assert BaseExecutionStrategy().supports_completion_enforcement() is False
        assert GenericExecutionStrategy().supports_completion_enforcement() is False

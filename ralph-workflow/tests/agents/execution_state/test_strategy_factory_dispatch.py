"""Black-box tests for transport-keyed strategy factory dispatch.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

import pytest

from ralph.agents.execution_state import (
    ClaudeExecutionStrategy,
    ClaudeInteractiveExecutionStrategy,
    CompletionEnforcingStrategy,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_transport,
)
from ralph.config.enums import AgentTransport
from ralph.process.child_liveness import ChildLivenessRegistry


class TestStrategyFactoryDispatch:
    """strategy_for_transport resolves strategy instances by AgentTransport."""

    @pytest.mark.parametrize(
        ("transport", "expected_class"),
        [
            (AgentTransport.OPENCODE, OpenCodeExecutionStrategy),
            (AgentTransport.CLAUDE, ClaudeExecutionStrategy),
            (AgentTransport.CLAUDE_INTERACTIVE, ClaudeInteractiveExecutionStrategy),
            (AgentTransport.AGY, CompletionEnforcingStrategy),
            (AgentTransport.CODEX, GenericExecutionStrategy),
            (AgentTransport.NANOCODER, GenericExecutionStrategy),
            (AgentTransport.GENERIC, GenericExecutionStrategy),
        ],
    )
    def test_returns_expected_strategy_for_each_transport(
        self,
        transport: AgentTransport,
        expected_class: type,
    ) -> None:
        strategy = strategy_for_transport(transport)
        assert isinstance(strategy, expected_class)

    def test_unknown_transport_falls_back_to_generic(self) -> None:
        strategy = strategy_for_transport("unknown")
        assert isinstance(strategy, GenericExecutionStrategy)

    def test_opencode_forwards_label_scope_and_registry(self) -> None:
        registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=10.0,
            stale_label_ttl=5.0,
            exit_reconcile=1.0,
        )
        strategy = strategy_for_transport(
            AgentTransport.OPENCODE,
            label_scope="unit-x",
            registry=registry,
        )
        assert isinstance(strategy, OpenCodeExecutionStrategy)
        strategy.record_native_child_progress("child-1")
        assert registry.has_child("child-1")
        assert registry.has_records("agent:unit-x:")

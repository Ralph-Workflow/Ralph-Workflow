"""Black-box tests for transport-keyed strategy factory dispatch.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import (
    AgyExecutionStrategy,
    ClaudeExecutionStrategy,
    ClaudeInteractiveExecutionStrategy,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_transport,
)
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry


class TestStrategyFactoryDispatch:
    """strategy_for_transport is a pure dict lookup by AgentTransport."""

    @pytest.mark.parametrize(
        ("transport", "expected_class"),
        [
            (AgentTransport.OPENCODE, OpenCodeExecutionStrategy),
            (AgentTransport.CLAUDE, ClaudeExecutionStrategy),
            (AgentTransport.CLAUDE_INTERACTIVE, ClaudeInteractiveExecutionStrategy),
            (AgentTransport.AGY, AgyExecutionStrategy),
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
        fake_registry = cast("ChildLivenessRegistry", object())
        strategy = strategy_for_transport(
            AgentTransport.OPENCODE,
            label_scope="unit-x",
            registry=fake_registry,
        )
        assert isinstance(strategy, OpenCodeExecutionStrategy)
        assert strategy._label_scope == "unit-x"
        assert strategy._registry is fake_registry

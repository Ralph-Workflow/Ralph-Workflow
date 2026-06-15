"""Black-box tests for transport-keyed strategy factory dispatch.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

import inspect
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
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
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

    def test_non_opencode_entries_are_direct_class_references(self) -> None:
        """The dispatch table uses direct class references except for OpenCode."""
        assert _STRATEGY_DISPATCH[AgentTransport.CLAUDE] is ClaudeExecutionStrategy
        assert (
            _STRATEGY_DISPATCH[AgentTransport.CLAUDE_INTERACTIVE]
            is ClaudeInteractiveExecutionStrategy
        )
        assert _STRATEGY_DISPATCH[AgentTransport.AGY] is AgyExecutionStrategy
        assert _STRATEGY_DISPATCH[AgentTransport.CODEX] is GenericExecutionStrategy
        assert _STRATEGY_DISPATCH[AgentTransport.NANOCODER] is GenericExecutionStrategy
        assert _STRATEGY_DISPATCH[AgentTransport.GENERIC] is GenericExecutionStrategy

    def test_strategy_for_transport_is_pure_dict_lookup(self) -> None:
        """The factory performs no branching beyond dict get/default."""
        source = inspect.getsource(strategy_for_transport)
        # The implementation must be a two-line dict lookup; no if/else branches.
        assert "if " not in source
        assert "else:" not in source

        # Any transport not in the dict falls back to the
        # GenericExecutionStrategy class reference used as the default.
        fallback = _STRATEGY_DISPATCH.get("not-a-transport", GenericExecutionStrategy)
        assert fallback is GenericExecutionStrategy

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

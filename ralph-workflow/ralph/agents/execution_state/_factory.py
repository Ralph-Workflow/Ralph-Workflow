"""Transport-keyed strategy factory.

To add a new transport, append one entry to ``_STRATEGY_DISPATCH``; the factory
itself never needs editing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.config.enums import AgentTransport

from .agy_execution_strategy import AgyExecutionStrategy
from .claude_execution_strategy import ClaudeExecutionStrategy
from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry

    from ._base import BaseExecutionStrategy


class _StrategyFactory(Protocol):
    """Factory that accepts the transport-keyed kwargs from strategy_for_transport."""

    def __call__(
        self,
        *,
        label_scope: str | None,
        registry: ChildLivenessRegistry | None,
    ) -> BaseExecutionStrategy:
        ...


def _make_opencode_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    return OpenCodeExecutionStrategy(label_scope=label_scope, registry=registry)


def _make_claude_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    del label_scope, registry
    return ClaudeExecutionStrategy()


def _make_claude_interactive_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    del label_scope, registry
    return ClaudeInteractiveExecutionStrategy()


def _make_agy_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    del label_scope, registry
    return AgyExecutionStrategy()


def _make_generic_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    del label_scope, registry
    return GenericExecutionStrategy()


_STRATEGY_DISPATCH: dict[AgentTransport, _StrategyFactory] = {
    AgentTransport.OPENCODE: _make_opencode_strategy,
    AgentTransport.CLAUDE: _make_claude_strategy,
    AgentTransport.CLAUDE_INTERACTIVE: _make_claude_interactive_strategy,
    AgentTransport.AGY: _make_agy_strategy,
    AgentTransport.CODEX: _make_generic_strategy,
    AgentTransport.NANOCODER: _make_generic_strategy,
    AgentTransport.GENERIC: _make_generic_strategy,
}


def strategy_for_transport(
    transport: object,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    """Return the appropriate ExecutionStrategy for an agent transport."""
    if isinstance(transport, AgentTransport):
        factory = _STRATEGY_DISPATCH.get(transport, _make_generic_strategy)
    else:
        factory = _make_generic_strategy
    return factory(label_scope=label_scope, registry=registry)

"""Transport-keyed strategy factory.

To add a new transport, append one entry to ``_STRATEGY_DISPATCH``; the factory
itself never needs editing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from ralph.config.enums import AgentTransport

from ..parsers import _CUSTOM_COMMAND_REGISTRY, _ParserRegistryEntry
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
    **_kwargs: object,
) -> BaseExecutionStrategy:
    """Forward transport kwargs to the OpenCode strategy constructor."""
    del _kwargs
    return OpenCodeExecutionStrategy(label_scope=label_scope, registry=registry)


_STRATEGY_DISPATCH: dict[AgentTransport, _StrategyFactory] = {
    AgentTransport.OPENCODE: _make_opencode_strategy,
    AgentTransport.CLAUDE: ClaudeExecutionStrategy,
    AgentTransport.CLAUDE_INTERACTIVE: ClaudeInteractiveExecutionStrategy,
    AgentTransport.AGY: AgyExecutionStrategy,
    AgentTransport.CODEX: GenericExecutionStrategy,
    AgentTransport.NANOCODER: GenericExecutionStrategy,
    AgentTransport.GENERIC: GenericExecutionStrategy,
}


def strategy_for_transport(
    transport: object,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    """Return the appropriate ExecutionStrategy for an agent transport."""
    # ``transport`` is typed as ``object`` for backward compatibility, but the
    # dispatch table is keyed by ``AgentTransport``.  The ``dict.get`` default
    # handles non-transport values at runtime; the cast keeps the lookup
    # type-safe for mypy without introducing branching.
    factory = _STRATEGY_DISPATCH.get(cast("AgentTransport", transport), GenericExecutionStrategy)
    return factory(label_scope=label_scope, registry=registry)


def strategy_for_command(
    cmd: str,
    transport: AgentTransport,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    """Return the execution strategy registered for ``cmd`` when one exists.

    Custom agents registered via ``register_agent_support()`` are keyed by
    their full executable command string.  When a matching entry exists and
    its registered transport matches ``transport``, its strategy factory is
    used; otherwise the transport-keyed fallback from
    :func:`strategy_for_transport` is used.
    """
    command_lower = cmd.lower() if cmd else ""
    entry = _CUSTOM_COMMAND_REGISTRY.get(command_lower)
    if isinstance(entry, _ParserRegistryEntry) and entry.transport == transport:
        return entry.strategy_factory(label_scope=label_scope, registry=registry)
    return strategy_for_transport(transport, label_scope=label_scope, registry=registry)

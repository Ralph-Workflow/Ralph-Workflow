"""Transport-keyed strategy factory.

To add a new transport, append one entry to ``_STRATEGY_DISPATCH``; the factory
itself never needs editing.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING, cast

from ralph.config.enums import AgentTransport

from ..parsers import _CUSTOM_COMMAND_REGISTRY, _ParserRegistryEntry
from ._completion_mixin import CompletionEnforcingStrategy
from .claude_execution_strategy import ClaudeExecutionStrategy
from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.agents._contracts import StrategyFactory
    from ralph.process.child_liveness import ChildLivenessRegistry

    from ._base import BaseExecutionStrategy


def _make_opencode_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    """Forward transport kwargs to the OpenCode strategy constructor."""
    del _kwargs
    return OpenCodeExecutionStrategy(label_scope=label_scope, registry=registry)


def _make_agy_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    """Factory for AGY strategy: CompletionEnforcingStrategy wrapping GenericExecutionStrategy."""

    class AgyExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        pass

    return AgyExecutionStrategy(label_scope=label_scope, registry=registry)


# DEPRECATED: write-through state populated atomically by AgentCatalog.add().
# New code should use ralph.agents.catalog.default_catalog() or construct an
# AgentCatalog explicitly. The dicts will be removed in a future release.
# Internal mutable storage (write target for AgentCatalog._write_through).
_STRATEGY_DISPATCH_DATA: dict[AgentTransport, StrategyFactory] = {
    AgentTransport.OPENCODE: _make_opencode_strategy,
    AgentTransport.CLAUDE: ClaudeExecutionStrategy,
    AgentTransport.CLAUDE_INTERACTIVE: ClaudeInteractiveExecutionStrategy,
    AgentTransport.AGY: _make_agy_strategy,
    AgentTransport.CODEX: GenericExecutionStrategy,
    AgentTransport.NANOCODER: GenericExecutionStrategy,
    AgentTransport.GENERIC: GenericExecutionStrategy,
}

# Public read-only view over the internal mutable dict.
_STRATEGY_DISPATCH: Mapping[AgentTransport, StrategyFactory] = types.MappingProxyType(
    _STRATEGY_DISPATCH_DATA
)


def strategy_for_transport(
    transport: object,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> BaseExecutionStrategy:
    """Return the appropriate ExecutionStrategy for an agent transport."""
    factory = _STRATEGY_DISPATCH.get(
        cast("AgentTransport", transport), GenericExecutionStrategy
    )
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

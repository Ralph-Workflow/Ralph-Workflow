"""Transport-keyed strategy resolution."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from ralph.agents import catalog
from ralph.agents.registry_types import ParserRegistryEntry
from ralph.config.enums import AgentTransport

from ._strategy_builders import (
    _make_agy_strategy,
    _make_cursor_strategy,
    _make_kimi_strategy,
    _make_pi_strategy,
)
from .claude_execution_strategy import ClaudeExecutionStrategy
from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

__all__ = [
    "_make_agy_strategy",
    "_make_cursor_strategy",
    "_make_kimi_strategy",
    "_make_pi_strategy",
    "strategy_for_command",
    "strategy_for_transport",
]


if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.agents._contracts import StrategyFactory
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.monitor import SubagentPidSource

    from ._base import BaseExecutionStrategy


class _CatalogState(Protocol):
    @property
    def commands(self) -> Mapping[str, object]: ...


class _Catalog(Protocol):
    @property
    def _state(self) -> _CatalogState: ...


def _default_catalog() -> _Catalog:
    return catalog.default_catalog()


_STRATEGY_DISPATCH: Mapping[AgentTransport, StrategyFactory] = MappingProxyType(
    {
        AgentTransport.OPENCODE: OpenCodeExecutionStrategy,
        AgentTransport.CLAUDE: ClaudeExecutionStrategy,
        AgentTransport.CLAUDE_INTERACTIVE: ClaudeInteractiveExecutionStrategy,
        AgentTransport.AGY: _make_agy_strategy,
        AgentTransport.CODEX: GenericExecutionStrategy,
        AgentTransport.NANOCODER: GenericExecutionStrategy,
        AgentTransport.PI: _make_pi_strategy,
        AgentTransport.CURSOR: _make_cursor_strategy,
        AgentTransport.KIMI: _make_kimi_strategy,
        AgentTransport.GENERIC: GenericExecutionStrategy,
    }
)


def _transport_key(transport: object) -> AgentTransport:
    if isinstance(transport, AgentTransport):
        return transport
    return AgentTransport.GENERIC


def strategy_for_transport(
    transport: object,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    transport_key = _transport_key(transport)
    factory = _STRATEGY_DISPATCH.get(transport_key, GenericExecutionStrategy)
    return factory(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def strategy_for_command(
    cmd: str,
    transport: AgentTransport,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    command_lower = cmd.lower() if cmd else ""
    custom_entry = _default_catalog()._state.commands.get(command_lower)
    if isinstance(custom_entry, ParserRegistryEntry) and custom_entry.transport == transport:
        return custom_entry.strategy_factory(
            label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
        )
    return strategy_for_transport(
        transport,
        label_scope=label_scope,
        registry=registry,
        subagent_pid_source=subagent_pid_source,
    )

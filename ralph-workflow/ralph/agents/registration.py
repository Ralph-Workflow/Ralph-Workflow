"""Unified one-call registration API for new agent support.

This module is opt-in: import it as
``from ralph.agents.registration import register_agent_support``.
It is intentionally NOT re-exported from ``ralph.agents`` so the public surface
stays small and the registration seam remains explicit.

Advanced use cases (CCS aliases, dynamic model parsing, custom
``AgentRegistry.ccs_defaults``) must still use ``AgentRegistry`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.agents.parsers import _PARSER_REGISTRY
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType

from .execution_state._factory import _STRATEGY_DISPATCH

if TYPE_CHECKING:
    from ralph.agents.parsers.base import AgentParser
    from ralph.process.child_liveness import ChildLivenessRegistry

    from .execution_state._base import BaseExecutionStrategy
    from .registry import AgentRegistry


class _ParserFactory(Protocol):
    """Callable that returns a fresh parser instance."""

    def __call__(self) -> AgentParser: ...


class _UserStrategyFactory(Protocol):
    """Callable supplied by callers of register_agent_support."""

    def __call__(self) -> BaseExecutionStrategy: ...


# Pure-data index that lets get_registered_agent_support recover the transport
# from the agent name. Values are immutable AgentTransport enum members.
_NAME_TRANSPORT_INDEX: dict[str, AgentTransport] = {}


def register_agent_support(
    name: str,
    *,
    transport: AgentTransport,
    parser_factory: _ParserFactory,
    strategy_factory: _UserStrategyFactory,
    agent_registry: AgentRegistry,
    json_parser: JsonParserType = JsonParserType.GENERIC,
    interactive: bool = False,
) -> AgentConfig:
    """Register support for a new agent in one call.

    Args:
        name: Agent name used by ``AgentRegistry`` and as the parser-type key.
        transport: Transport enum value that selects the execution strategy.
        parser_factory: Callable returning a parser instance for this agent.
        strategy_factory: Callable returning an execution strategy instance.
        agent_registry: Registry that owns the agent-name-keyed configuration.
        json_parser: Parser type token stored in ``AgentConfig.json_parser``.
        interactive: When True, sets ``AgentConfig.session_flag`` to a resume
            template so session continuation is available.

    Returns:
        The registered ``AgentConfig``.
    """
    _PARSER_REGISTRY[name] = parser_factory
    # Wrap the user factory so strategy_for_transport() can pass label_scope/registry
    # kwargs without requiring every custom strategy to accept them.
    def _wrapped_factory(
        *,
        label_scope: str | None = None,
        registry: ChildLivenessRegistry | None = None,
        **_kwargs: object,
    ) -> BaseExecutionStrategy:
        del label_scope, registry, _kwargs
        return strategy_factory()

    _STRATEGY_DISPATCH[transport] = _wrapped_factory
    _NAME_TRANSPORT_INDEX[name] = transport

    config = AgentConfig(
        cmd=name,
        json_parser=json_parser,
        transport=transport,
        session_flag="--resume {}" if interactive else None,
    )
    agent_registry.register(name, config)
    return config


def get_registered_agent_support(
    name: str,
) -> tuple[AgentParser, BaseExecutionStrategy] | None:
    """Return the registered parser instance and strategy instance for ``name``.

    Args:
        name: Agent / parser-type name.

    Returns:
        A ``(parser, strategy)`` tuple, or ``None`` if either piece is missing.
    """
    parser_factory = _PARSER_REGISTRY.get(name)
    transport = _NAME_TRANSPORT_INDEX.get(name)
    if parser_factory is None or transport is None:
        return None
    strategy_factory = _STRATEGY_DISPATCH.get(transport)
    if strategy_factory is None:
        return None
    return parser_factory(), strategy_factory(label_scope=None, registry=None)

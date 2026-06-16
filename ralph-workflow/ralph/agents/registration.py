"""Unified one-call registration API for new agent support.

This module is opt-in: import it as
``from ralph.agents.registration import register_agent_support``.
It is intentionally NOT re-exported from ``ralph.agents`` so the public surface
stays small and the registration seam remains explicit.

Advanced use cases (CCS aliases, dynamic model parsing, custom
``AgentRegistry.ccs_defaults``) must still use ``AgentRegistry`` directly.

The function writes into three existing lookup tables:

* ``ralph.agents.parsers._PARSER_REGISTRY`` — parser-type-keyed registry.
* ``ralph.agents.execution_state._factory._STRATEGY_DISPATCH`` — transport-keyed
  strategy registry used by :func:`strategy_for_transport`.
* ``ralph.agents.parsers._CUSTOM_COMMAND_REGISTRY`` — command-keyed registry used
  by :func:`strategy_for_command` and parser resolution at runtime.

The agent-name-keyed configuration is stored in the caller's own
``AgentRegistry``; the function never creates a throwaway registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents._contracts import StrategyFactory
from ralph.agents.catalog import default_catalog
from ralph.agents.support import AgentSupport
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.agents.parsers.base import AgentParser
    from ralph.config.agent_config import AgentConfig

    from .catalog import AgentCatalog
    from .registry import AgentRegistry


class _ParserFactory(Protocol):
    """Callable that returns a fresh parser instance."""

    def __call__(self) -> AgentParser: ...


class _AnyKwargsStrategyFactory(Protocol):
    """Internal cast target used to forward a dynamic kwargs dict."""

    def __call__(self, **kwargs: object) -> object: ...


_UserStrategyFactory = StrategyFactory | object


def register_agent_support(
    name: str,
    *,
    transport: AgentTransport,
    parser_factory: _ParserFactory,
    strategy_factory: _UserStrategyFactory,
    agent_registry: AgentRegistry,
    json_parser: JsonParserType = JsonParserType.GENERIC,
    interactive: bool = False,
    cmd: str | None = None,
    output_flag: str | None = None,
    yolo_flag: str | None = None,
    verbose_flag: str | None = None,
    can_commit: bool = False,
    model_flag: str | None = None,
    print_flag: str | None = None,
    streaming_flag: str | None = None,
    session_flag: str | None = None,
    display_name: str | None = None,
    subagent_capability: bool | None = None,
) -> AgentConfig:
    """Register support for a new agent in one call.

    Delegates to ``AgentSupport.from_registration_kwargs`` +
    ``default_catalog().add(support)``. The legacy module-level dicts
    (``_PARSER_REGISTRY``, ``_CUSTOM_COMMAND_REGISTRY``,
    ``_STRATEGY_DISPATCH``) are write-through state populated atomically by
    ``AgentCatalog.add()`` and are deprecated; new code should use
    ``AgentCatalog`` directly.

    Args:
        name: Agent name used by ``AgentRegistry`` and as the parser-type key.
        transport: Transport enum value that selects the execution strategy.
        parser_factory: Callable returning a parser instance for this agent.
        strategy_factory: Callable returning an execution strategy instance.
        agent_registry: Registry that owns the agent-name-keyed configuration.
        json_parser: Parser type token stored in ``AgentConfig.json_parser``.
        interactive: When True and ``session_flag`` is not provided, sets a
            default resume template so session continuation is available.
        cmd: Executable command for the agent; defaults to ``name``.
        output_flag: Optional output format flag for streaming JSON.
        yolo_flag: Optional autonomous/non-interactive flag string.
        verbose_flag: Optional verbose flag string.
        can_commit: Whether the agent can run git commit.
        model_flag: Optional model/provider flag.
        print_flag: Optional print flag for non-interactive output mode.
        streaming_flag: Optional streaming flag for partial JSON messages.
        session_flag: Optional session continuation flag template.
        display_name: Human-readable display name for UI/UX.
        subagent_capability: Whether the agent runtime exposes usable sub-agent
            tooling.

    Returns:
        The registered ``AgentConfig``.

    Raises:
        ValueError: If ``name`` matches a reserved built-in parser key, or if
            ``cmd`` is already registered for a different agent.
    """
    support = AgentSupport.from_registration_kwargs(
        name,
        transport=transport,
        parser_factory=parser_factory,
        strategy_factory=cast("StrategyFactory", strategy_factory),
        agent_registry=agent_registry,
        json_parser=json_parser,
        interactive=interactive,
        cmd=cmd,
        output_flag=output_flag,
        yolo_flag=yolo_flag,
        verbose_flag=verbose_flag,
        can_commit=can_commit,
        model_flag=model_flag,
        print_flag=print_flag,
        streaming_flag=streaming_flag,
        session_flag=session_flag,
        display_name=display_name,
        subagent_capability=subagent_capability,
    )

    default_catalog().add(support)
    agent_registry.register(name, support.config)
    return support.config


def register_agent_support_to_catalog(
    name: str,
    support: AgentSupport,
    catalog: AgentCatalog,
) -> AgentConfig:
    """Register an AgentSupport into a specific catalog (test-friendly entry point).

    Args:
        name: Agent name.
        support: Pre-built AgentSupport instance.
        catalog: Target AgentCatalog.

    Returns:
        The registered ``AgentConfig``.
    """
    catalog.add(support)
    return support.config


def get_registered_agent_support(
    name: str,
) -> tuple[AgentParser, BaseExecutionStrategy] | None:
    """Return the registered parser instance and strategy instance for ``name``.

    Args:
        name: Agent / parser-type name.

    Returns:
        A ``(parser, strategy)`` tuple, or ``None`` if the name was not
        registered through this API.
    """
    support = default_catalog().get(name)
    if support is None:
        return None
    return support.parser_factory(), support.strategy_factory(
        label_scope=None, registry=None
    )

"""Unified one-call registration API for new agent support.

This module is the canonical home of :func:`register_agent_support`. It is
also re-exported from the public ``ralph.agents`` surface (see
``ralph.agents.__all__`` and ``__getattr__``), so callers may import it as
either of:

* ``from ralph.agents import register_agent_support`` (preferred — public)
* ``from ralph.agents.registration import register_agent_support`` (direct)

The re-export keeps the registration seam discoverable from the package
root while still pointing at this module for the implementation.

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
from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.support import AgentSupport
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.agents.parsers.base import AgentParser
    from ralph.config.agent_config import AgentConfig

    from .registry import AgentRegistry


class _ParserFactory(Protocol):
    """Callable that returns a fresh parser instance."""

    def __call__(self) -> AgentParser: ...


class _AnyKwargsStrategyFactory(Protocol):
    """Internal cast target used to forward a dynamic kwargs dict."""

    def __call__(self, **kwargs: object) -> object: ...


_UserStrategyFactory = StrategyFactory | object


# ---------------------------------------------------------------------------
# Default-strategy dispatch
#
# The canonical source of truth for the transport-to-strategy dispatch
# table is :data:`AgentCatalog._DEFAULT_STRATEGIES` (in
# :mod:`ralph.agents.catalog`).  This module exposes a thin helper,
# :func:`_default_strategy_for_transport`, that reads from that single
# source of truth.  No duplicate table is maintained here.
# ---------------------------------------------------------------------------


def _default_strategy_for_transport(transport: AgentTransport) -> StrategyFactory:
    """Return the transport-derived default strategy for ``transport``.

    Looks up :data:`AgentCatalog._DEFAULT_STRATEGIES` (the canonical
    source of truth).  Raises ``ValueError`` if no default is registered.
    """
    catalog = default_catalog()
    factory = catalog._DEFAULT_STRATEGIES.get(transport)
    if factory is None:
        msg = f"No default strategy registered for transport {transport!r}"
        raise ValueError(msg)
    return factory


# ---------------------------------------------------------------------------
# Unified registration helpers
#
# All three public registration helpers route through
# :func:`_validate_and_materialize_support` (which builds the
# ``AgentSupport``) and :func:`_register_to_catalog` (the sole
# ``AgentCatalog.add()`` call site in the registration code path).
# This guarantees the single-call-per-registration contract pinned by
# ``test_registration_shim.py::TestRegistrationDelegationToAgentCatalog``.
# ---------------------------------------------------------------------------


def _validate_and_materialize_support(
    name: str,
    transport: AgentTransport,
    parser_factory: _ParserFactory,
    strategy_factory: _UserStrategyFactory,
    agent_registry: AgentRegistry | object,
    json_parser: JsonParserType,
    interactive: bool,
    cmd: str | None,
    output_flag: str | None,
    yolo_flag: str | None,
    verbose_flag: str | None,
    can_commit: bool,
    model_flag: str | None,
    print_flag: str | None,
    streaming_flag: str | None,
    session_flag: str | None,
    display_name: str | None,
    subagent_capability: bool | None,
    no_default_session_flag: bool,
    is_builtin: bool = False,
) -> AgentSupport:
    """Build an :class:`AgentSupport` from the registration kwargs.

    Single source of truth for the kwargs-to-support translation.  All
    three public registration helpers delegate to this function so the
    14-kwarg surface is defined in exactly one place.
    """
    return AgentSupport.from_registration_kwargs(
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
        is_builtin=is_builtin,
        no_default_session_flag=no_default_session_flag,
    )


def _register_to_catalog(name: str, support: AgentSupport, catalog: AgentCatalog) -> AgentConfig:
    """Add ``support`` to ``catalog`` and return the resulting config.

    Sole ``AgentCatalog.add()`` call site in the registration code
    path.  ``register_agent_support``, ``register_agent_support_to_catalog``,
    and ``register_my_agent`` all delegate here.
    """
    catalog.add(support)
    return support.config


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
    no_default_session_flag: bool = False,
) -> AgentConfig:
    """Register support for a new agent in one call.

    Delegates to :func:`_validate_and_materialize_support` +
    :func:`_register_to_catalog` so the registration is written
    through the caller-owned catalog bound to ``agent_registry``, not the
    global default catalog.  When ``agent_registry`` was constructed without
    an explicit ``catalog=``, that bound catalog is
    :func:`ralph.agents.catalog.default_catalog`, so the historical
    behavior is preserved.  When ``agent_registry=AgentRegistry(catalog=AgentCatalog())``
    is used, the registration stays inside the caller-owned catalog and does
    not leak into the default catalog.  The legacy module-level dicts
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
    support = _validate_and_materialize_support(
        name,
        transport,
        parser_factory,
        strategy_factory,
        agent_registry,
        json_parser,
        interactive,
        cmd,
        output_flag,
        yolo_flag,
        verbose_flag,
        can_commit,
        model_flag,
        print_flag,
        streaming_flag,
        session_flag,
        display_name,
        subagent_capability,
        no_default_session_flag,
    )

    _register_to_catalog(name, support, agent_registry.catalog)
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
    return _register_to_catalog(name, support, catalog)


def register_my_agent(
    name: str,
    transport: AgentTransport,
    *,
    parser: type[AgentParser],
    strategy: StrategyFactory | None = None,
    agent_registry: AgentRegistry,
    interactive: bool = False,
    cmd: str | None = None,
    session_flag: str | None = None,
    no_default_session_flag: bool = False,
    json_parser: JsonParserType = JsonParserType.GENERIC,
) -> AgentConfig:
    """Opinionated 5-line recipe for adding a new agent.

    The helper picks the strategy from
    :data:`AgentCatalog._DEFAULT_STRATEGIES` when ``strategy`` is not
    provided, so an interactive caller can never accidentally register an
    interactive agent with :class:`BaseExecutionStrategy`.  For interactive
    agents, the helper also auto-applies the ``--resume {}`` session
    template unless ``no_default_session_flag=True`` is passed.

    Args:
        name: Agent name.
        transport: Transport enum value.
        parser: Parser class to register.
        strategy: Optional strategy factory.  When ``None``, the helper
            picks the transport-derived default.
        agent_registry: Registry that owns the agent-name-keyed configuration.
        interactive: Whether the agent is interactive (PTY).
        cmd: Executable command; defaults to ``name``.
        session_flag: Optional session continuation flag template.  When
            ``None`` and the agent is interactive, ``--resume {}`` is
            auto-applied unless ``no_default_session_flag=True``.
        no_default_session_flag: Suppress the default ``--resume {}``
            template.  Used by agy.
        json_parser: Parser type token.

    Returns:
        The registered :class:`AgentConfig`.
    """
    resolved_strategy = (
        strategy if strategy is not None else _default_strategy_for_transport(transport)
    )

    return register_agent_support(
        name,
        transport=transport,
        parser_factory=cast("_ParserFactory", parser),
        strategy_factory=resolved_strategy,
        agent_registry=agent_registry,
        json_parser=json_parser,
        interactive=interactive,
        cmd=cmd,
        session_flag=session_flag,
        no_default_session_flag=no_default_session_flag,
    )


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
    return support.parser_factory(), support.strategy_factory(label_scope=None, registry=None)

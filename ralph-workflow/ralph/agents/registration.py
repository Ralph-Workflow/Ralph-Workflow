"""Unified one-call registration API for new agent support.

This module is opt-in: import it as
``from ralph.agents.registration import register_agent_support``.
It is intentionally NOT re-exported from ``ralph.agents`` so the public surface
stays small and the registration seam remains explicit.

Advanced use cases (CCS aliases, dynamic model parsing, custom
``AgentRegistry.ccs_defaults``) must still use ``AgentRegistry`` directly.

The function writes into the existing parser-type registry
(``ralph.agents.parsers._PARSER_REGISTRY``) and a collision-free custom-command
registry (``ralph.agents.parsers._CUSTOM_COMMAND_REGISTRY``) keyed by the full
executable command string.  Multiple custom agents may share a transport; each
agent keeps its own parser and strategy entry keyed by both its registered
``name`` and its full command.  Runtime invocation uses
:func:`ralph.agents.execution_state.strategy_for_command`, which selects the
strategy registered for the agent's full command before falling back to the
transport-keyed ``strategy_for_transport`` slot.  The transport-keyed slot is
never overwritten by a custom registration, so unrelated commands on the same
transport continue to use the built-in fallback.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers import (
    _CUSTOM_COMMAND_REGISTRY,
    _PARSER_REGISTRY,
    _ParserRegistryEntry,
)
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents.parsers.base import AgentParser
    from ralph.process.child_liveness import ChildLivenessRegistry

    from .execution_state._factory import _StrategyFactory
    from .registry import AgentRegistry


class _ParserFactory(Protocol):
    """Callable that returns a fresh parser instance."""

    def __call__(self) -> AgentParser: ...


class _StrategyFactoryWithKwargs(Protocol):
    """Factory that accepts the runtime kwargs forwarded by ``strategy_for_transport``."""

    def __call__(
        self,
        *,
        label_scope: str | None,
        registry: ChildLivenessRegistry | None,
    ) -> BaseExecutionStrategy: ...


class _AnyKwargsStrategyFactory(Protocol):
    """Internal cast target used to forward a dynamic kwargs dict."""

    def __call__(self, **kwargs: object) -> BaseExecutionStrategy: ...


_UserStrategyFactory = type[BaseExecutionStrategy] | _StrategyFactoryWithKwargs


def _wrap_strategy_factory(
    factory: _UserStrategyFactory,
) -> _StrategyFactory:
    """Wrap a user strategy factory so transport kwargs are preserved when accepted.

    ``strategy_for_transport`` forwards ``label_scope`` and ``registry`` to every
    factory.  Custom factories that do not accept those kwargs (for example a
    plain ``BaseExecutionStrategy`` subclass) still work: the wrapper only
    forwards kwargs the underlying callable actually accepts, including
    factories that accept arbitrary kwargs via ``**kwargs``.
    """
    sig = inspect.signature(factory)
    params = sig.parameters
    accepts_label_scope = "label_scope" in params
    accepts_registry = "registry" in params
    # ``inspect.Parameter.VAR_KEYWORD`` is 4; using the literal avoids an
    # ``Any``-typed expression from the ``inspect`` stubs under mypy strict.
    _var_keyword_kind = 4
    accepts_any_kwargs = any(
        param.kind == _var_keyword_kind for param in params.values()
    )

    def wrapped(
        *,
        label_scope: str | None = None,
        registry: ChildLivenessRegistry | None = None,
        **_kwargs: object,
    ) -> BaseExecutionStrategy:
        kwargs: dict[str, object] = {}
        if accepts_label_scope or accepts_any_kwargs:
            kwargs["label_scope"] = label_scope
        if accepts_registry or accepts_any_kwargs:
            kwargs["registry"] = registry
        # Runtime inspection guarantees we only forward kwargs the underlying
        # factory accepts, so the cast is safe.
        return cast("_AnyKwargsStrategyFactory", factory)(**kwargs)

    return wrapped


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
        session_flag: Optional session continuation flag template.  When
            ``interactive=True`` and this is omitted, a ``--resume {}`` template
            is used.
        display_name: Human-readable display name for UI/UX.
        subagent_capability: Whether the agent runtime exposes usable sub-agent
            tooling.

    Returns:
        The registered ``AgentConfig``.
    """
    effective_session_flag = session_flag
    if effective_session_flag is None and interactive:
        effective_session_flag = "--resume {}"

    config = AgentConfig(
        cmd=cmd if cmd is not None else name,
        output_flag=output_flag,
        yolo_flag=yolo_flag,
        verbose_flag=verbose_flag,
        can_commit=can_commit,
        json_parser=json_parser,
        model_flag=model_flag,
        print_flag=print_flag,
        streaming_flag=streaming_flag,
        session_flag=effective_session_flag,
        display_name=display_name,
        transport=transport,
        subagent_capability=subagent_capability,
    )

    wrapped_strategy = _wrap_strategy_factory(strategy_factory)
    entry = _ParserRegistryEntry(parser_factory, wrapped_strategy, transport)
    _PARSER_REGISTRY[name] = entry
    if config.cmd:
        _CUSTOM_COMMAND_REGISTRY[config.cmd.lower()] = entry
    agent_registry.register(name, config)
    return config


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
    entry = _PARSER_REGISTRY.get(name)
    if not isinstance(entry, _ParserRegistryEntry):
        return None
    return entry.parser_factory(), entry.strategy_factory(label_scope=None, registry=None)

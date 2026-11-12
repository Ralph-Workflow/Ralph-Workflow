from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents._contracts import StrategyFactory
from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.support import AgentSupport
from ralph.agents.vision_agent_provisioning import provision_vision_verdict_agent
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.agents.parsers.base import AgentParser
    from ralph.config.agent_config import AgentConfig
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

    from .registry import AgentRegistry


class _ParserFactory(Protocol):
    def __call__(self) -> AgentParser: ...


class _AnyKwargsStrategyFactory(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


_UserStrategyFactory = StrategyFactory | object


def _default_strategy_for_transport(transport: AgentTransport) -> StrategyFactory:
    catalog = default_catalog()
    factory = catalog._DEFAULT_STRATEGIES.get(transport)
    if factory is None:
        msg = f"No default strategy registered for transport {transport!r}"
        raise ValueError(msg)
    return factory


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
    model_flag: str | None,
    print_flag: str | None,
    streaming_flag: str | None,
    session_flag: str | None,
    display_name: str | None,
    subagent_capability: bool | None,
    no_default_session_flag: bool,
    is_builtin: bool = False,
    dynamic_alias_help: Callable[[], str] | None = None,
    dynamic_alias_help_prefix: tuple[str, ...] = (),
    empty_output_diagnostic_factory: (Callable[[list[str], Path | None], str | None] | None) = None,
    empty_output_diagnostic_prefix: tuple[str, ...] = (),
) -> AgentSupport:
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
        model_flag=model_flag,
        print_flag=print_flag,
        streaming_flag=streaming_flag,
        session_flag=session_flag,
        display_name=display_name,
        subagent_capability=subagent_capability,
        is_builtin=is_builtin,
        no_default_session_flag=no_default_session_flag,
        dynamic_alias_help=dynamic_alias_help,
        dynamic_alias_help_prefix=dynamic_alias_help_prefix,
        empty_output_diagnostic_factory=empty_output_diagnostic_factory,
        empty_output_diagnostic_prefix=empty_output_diagnostic_prefix,
    )


def _register_to_catalog(name: str, support: AgentSupport, catalog: AgentCatalog) -> AgentConfig:
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
    model_flag: str | None = None,
    print_flag: str | None = None,
    streaming_flag: str | None = None,
    session_flag: str | None = None,
    display_name: str | None = None,
    subagent_capability: bool | None = None,
    no_default_session_flag: bool = False,
    dynamic_alias_help: Callable[[], str] | None = None,
    dynamic_alias_help_prefix: tuple[str, ...] = (),
    empty_output_diagnostic_factory: (Callable[[list[str], Path | None], str | None] | None) = None,
    empty_output_diagnostic_prefix: tuple[str, ...] = (),
) -> AgentConfig:
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
        model_flag,
        print_flag,
        streaming_flag,
        session_flag,
        display_name,
        subagent_capability,
        no_default_session_flag,
        dynamic_alias_help=dynamic_alias_help,
        dynamic_alias_help_prefix=dynamic_alias_help_prefix,
        empty_output_diagnostic_factory=empty_output_diagnostic_factory,
        empty_output_diagnostic_prefix=empty_output_diagnostic_prefix,
    )

    _register_to_catalog(name, support, agent_registry.catalog)
    agent_registry.register(name, support.config)
    return support.config


def register_agent_support_to_catalog(
    name: str,
    support: AgentSupport,
    catalog: AgentCatalog,
) -> AgentConfig:
    return _register_to_catalog(name, support, catalog)


def register_vision_verdict_agent(
    catalog: AgentCatalog,
    *,
    workspace: Workspace | None = None,
    stack: ProjectStack | None = None,
) -> AgentConfig | None:
    if not provision_vision_verdict_agent(catalog, workspace=workspace, stack=stack):
        return None
    support = catalog.get("vision-verdict")
    if support is None:
        return None
    return support.config


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
    support = default_catalog().get(name)
    if support is None:
        return None
    return support.parser_factory(), support.strategy_factory(label_scope=None, registry=None)

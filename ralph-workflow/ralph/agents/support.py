from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from ralph.agents.display_capabilities import DisplayCapability, all_display_capabilities
from ralph.agents.display_capability_stance import DisplayCapabilityStance
from ralph.agents.spec import AgentSpec
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents._contracts import StrategyFactory
    from ralph.agents.parsers.base import AgentParser


_DYNAMIC_ALIAS_HELP_BY_PREFIX: dict[str, Callable[[], str]] = {}

_EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX: dict[
    str, Callable[[list[str], Path | None], str | None]
] = {}


@dataclass(frozen=True, slots=True)
class AgentSupport:
    name: str
    spec: AgentSpec
    parser_factory: Callable[[], AgentParser]
    strategy_factory: StrategyFactory
    config: AgentConfig
    is_builtin: bool = False
    no_default_session_flag: bool = False
    session_identifier_observable: bool = True
    display_capabilities: tuple[DisplayCapabilityStance, ...] = ()
    dynamic_alias_help: Callable[[], str] | None = None
    dynamic_alias_help_prefix: tuple[str, ...] = ()
    empty_output_diagnostic_factory: Callable[[list[str], Path | None], str | None] | None = None
    empty_output_diagnostic_prefix: tuple[str, ...] = ()

    _name_lower: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "_name_lower", self.name.lower())
        _validate_display_capabilities(self.display_capabilities, is_builtin=self.is_builtin)

    @property
    def cmd(self) -> str:
        return self.config.cmd

    @property
    def transport(self) -> AgentTransport:
        return self.spec.transport

    def capability(self, capability: object) -> DisplayCapabilityStance | None:
        if isinstance(capability, DisplayCapability):
            value: str = capability.value
        else:
            if not isinstance(capability, str):
                return None
            value = capability
        for stance in self.display_capabilities:
            if isinstance(stance, DisplayCapabilityStance) and stance.capability.value == value:
                return stance
        return None

    @classmethod
    def from_registration_kwargs(
        cls,
        name: str,
        *,
        transport: AgentTransport,
        parser_factory: Callable[[], AgentParser],
        strategy_factory: StrategyFactory,
        agent_registry: object = None,
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
        is_builtin: bool = False,
        no_default_session_flag: bool = False,
        session_identifier_observable: bool = True,
        display_capabilities: tuple[DisplayCapabilityStance, ...] = (),
        dynamic_alias_help: Callable[[], str] | None = None,
        dynamic_alias_help_prefix: tuple[str, ...] = (),
        empty_output_diagnostic_factory: (
            Callable[[list[str], Path | None], str | None] | None
        ) = None,
        empty_output_diagnostic_prefix: tuple[str, ...] = (),
    ) -> AgentSupport:
        effective_session_flag = session_flag
        if effective_session_flag is None and interactive and not no_default_session_flag:
            effective_session_flag = "--resume {}"

        config = AgentConfig(
            cmd=cmd if cmd is not None else name,
            output_flag=output_flag,
            yolo_flag=yolo_flag,
            verbose_flag=verbose_flag,
            json_parser=json_parser,
            model_flag=model_flag,
            print_flag=print_flag,
            streaming_flag=streaming_flag,
            session_flag=effective_session_flag,
            display_name=display_name,
            transport=transport,
            subagent_capability=subagent_capability,
        )

        spec = AgentSpec.from_agent_config(
            config,
            interactive=interactive,
            completion_required=bool(effective_session_flag),
            no_default_session_flag=no_default_session_flag,
        )

        support = cls(
            name=name,
            spec=spec,
            parser_factory=parser_factory,
            strategy_factory=strategy_factory,
            config=config,
            is_builtin=is_builtin,
            no_default_session_flag=no_default_session_flag,
            session_identifier_observable=session_identifier_observable,
            display_capabilities=display_capabilities,
            dynamic_alias_help=dynamic_alias_help,
            dynamic_alias_help_prefix=dynamic_alias_help_prefix,
            empty_output_diagnostic_factory=empty_output_diagnostic_factory,
            empty_output_diagnostic_prefix=empty_output_diagnostic_prefix,
        )
        object.__setattr__(config, "_support", support)
        if dynamic_alias_help is not None:
            for prefix in dynamic_alias_help_prefix:
                _DYNAMIC_ALIAS_HELP_BY_PREFIX[prefix] = dynamic_alias_help
        if empty_output_diagnostic_factory is not None:
            for prefix in empty_output_diagnostic_prefix:
                _EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX[prefix] = empty_output_diagnostic_factory
        return support


def _validate_display_capabilities(
    stances: tuple[DisplayCapabilityStance, ...],
    *,
    is_builtin: bool,
) -> None:
    required = tuple(all_display_capabilities())
    if is_builtin:
        if not stances:
            msg = (
                "Built-in AgentSupport must declare display_capabilities "
                "covering every catalog-derived capability; got an empty tuple"
            )
            raise ValueError(msg)
        seen: set[str] = set()
        for stance in stances:
            if stance.capability in seen:
                msg = (
                    f"Duplicate display_capabilities entry for "
                    f"{stance.capability.name!r}; built-in agents must declare "
                    f"exactly one stance per capability"
                )
                raise ValueError(msg)
            seen.add(stance.capability)
        declared = {stance.capability for stance in stances}
        missing = [c for c in required if c not in declared]
        if missing:
            names = ", ".join(repr(c.name) for c in missing)
            msg = (
                f"Built-in AgentSupport is missing display_capabilities for "
                f"{names}; every catalog-derived capability must be declared"
            )
            raise ValueError(msg)
        extra = declared - set(required)
        if extra:
            names = ", ".join(repr(c.name) for c in extra)
            msg = (
                f"Built-in AgentSupport carries display_capabilities not in "
                f"the catalog-derived vocabulary: {names}"
            )
            raise ValueError(msg)
    else:
        for stance in stances:
            if stance.capability not in set(required):
                msg = (
                    f"Custom-agent display_capabilities entry "
                    f"{stance.capability!r} is not in the catalog-derived vocabulary"
                )
                raise ValueError(msg)
        seen_custom: set[str] = set()
        for stance in stances:
            if stance.capability in seen_custom:
                msg = (
                    f"Duplicate display_capabilities entry for "
                    f"{stance.capability.name!r}; each capability must appear at most once"
                )
                raise ValueError(msg)
            seen_custom.add(stance.capability)


def vision_verdict_agent_support(name: str = "vision-verdict") -> AgentSupport:
    from ralph.agents.builtin_spec import (
        vision_verdict_agent_spec,
    )

    return vision_verdict_agent_spec().to_support(name)

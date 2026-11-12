from __future__ import annotations

import dataclasses
from dataclasses import Field, dataclass
from typing import TYPE_CHECKING, cast

from ralph.agents.support import AgentSupport
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents._contracts import StrategyFactory
    from ralph.agents.display_capability_stance import DisplayCapabilityStance
    from ralph.agents.parsers.base import AgentParser


_BUILTIN_SPEC_POSITIONAL_FIELDS: frozenset[str] = frozenset(
    {"transport", "parser_factory", "strategy_factory"}
)

_BUILTIN_SPEC_FORWARDED_FIELDS: frozenset[str] = frozenset(
    {
        "cmd",
        "display_capabilities",
        "display_name",
        "dynamic_alias_help",
        "dynamic_alias_help_prefix",
        "empty_output_diagnostic_factory",
        "empty_output_diagnostic_prefix",
        "interactive",
        "json_parser",
        "model_flag",
        "no_default_session_flag",
        "output_flag",
        "print_flag",
        "session_flag",
        "session_identifier_observable",
        "streaming_flag",
        "subagent_capability",
        "verbose_flag",
        "yolo_flag",
    }
)


@dataclass(frozen=True, slots=True)
class BuiltinAgentSpec:
    transport: AgentTransport
    parser_factory: Callable[[], AgentParser]
    strategy_factory: StrategyFactory
    json_parser: JsonParserType = JsonParserType.GENERIC
    cmd: str | None = None
    output_flag: str | None = None
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    model_flag: str | None = None
    print_flag: str | None = None
    streaming_flag: str | None = None
    session_flag: str | None = None
    display_name: str | None = None
    interactive: bool = False
    subagent_capability: bool | None = None
    no_default_session_flag: bool = False
    session_identifier_observable: bool = True
    display_capabilities: tuple[DisplayCapabilityStance, ...] = ()
    dynamic_alias_help: Callable[[], str] | None = None
    dynamic_alias_help_prefix: tuple[str, ...] = ()
    empty_output_diagnostic_factory: Callable[[list[str], Path | None], str | None] | None = None
    empty_output_diagnostic_prefix: tuple[str, ...] = ()

    def to_support(self, name: str) -> AgentSupport:
        spec_fields = frozenset(
            spec_field.name
            for spec_field in cast("tuple[Field[str], ...]", dataclasses.fields(self))
        )
        forwarded = spec_fields - _BUILTIN_SPEC_POSITIONAL_FIELDS
        if forwarded != _BUILTIN_SPEC_FORWARDED_FIELDS:
            msg = (
                f"BuiltinAgentSpec fields {sorted(forwarded)} do not match the "
                f"registration fields forwarded to AgentSupport "
                f"{sorted(_BUILTIN_SPEC_FORWARDED_FIELDS)}"
            )
            raise ValueError(msg)
        return AgentSupport.from_registration_kwargs(
            name,
            transport=self.transport,
            parser_factory=self.parser_factory,
            strategy_factory=self.strategy_factory,
            json_parser=self.json_parser,
            cmd=self.cmd,
            output_flag=self.output_flag,
            yolo_flag=self.yolo_flag,
            verbose_flag=self.verbose_flag,
            model_flag=self.model_flag,
            print_flag=self.print_flag,
            streaming_flag=self.streaming_flag,
            session_flag=self.session_flag,
            display_name=self.display_name,
            interactive=self.interactive,
            subagent_capability=self.subagent_capability,
            no_default_session_flag=self.no_default_session_flag,
            session_identifier_observable=self.session_identifier_observable,
            display_capabilities=self.display_capabilities,
            dynamic_alias_help=self.dynamic_alias_help,
            dynamic_alias_help_prefix=self.dynamic_alias_help_prefix,
            empty_output_diagnostic_factory=self.empty_output_diagnostic_factory,
            empty_output_diagnostic_prefix=self.empty_output_diagnostic_prefix,
            is_builtin=True,
        )


def vision_verdict_agent_spec() -> BuiltinAgentSpec:
    from ralph.agents.display_capabilities import (
        DisplayCapability,
    )
    from ralph.agents.display_capability_stance import (
        DisplayCapabilityStance,
    )
    from ralph.agents.execution_state.generic_execution_strategy import (
        GenericExecutionStrategy,
    )
    from ralph.agents.parsers.generic import (
        GenericParser,
    )
    from ralph.config.enums import (
        AgentTransport,
        JsonParserType,
    )

    display_capabilities: tuple[DisplayCapabilityStance, ...] = (
        DisplayCapabilityStance.not_applicable(
            DisplayCapability.SYNTAX_HIGHLIGHTING,
            reason=(
                "vision-verdict is an in-process vision judge; it does not produce a TUI surface"
            ),
        ),
        DisplayCapabilityStance.not_applicable(
            DisplayCapability.FILE_PREVIEW,
            reason=("vision-verdict emits a DesignVerdict artifact, not a file preview"),
        ),
        DisplayCapabilityStance.not_applicable(
            DisplayCapability.EDIT_DIFF,
            reason=(
                "vision-verdict does not edit files; its judgement is "
                "captured in the verdict artifact, not a diff surface"
            ),
        ),
    )
    return BuiltinAgentSpec(
        transport=AgentTransport.GENERIC,
        parser_factory=GenericParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.GENERIC,
        cmd="vision-verdict",
        display_name="Vision Verdict",
        display_capabilities=display_capabilities,
    )

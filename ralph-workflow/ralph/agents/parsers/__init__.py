from __future__ import annotations

import importlib
import sys
import types
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ralph.config.enums import AgentTransport, JsonParserType

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .agy import AgyParser
from .base import AgentParser
from .claude import ClaudeParser
from .claude_interactive import ClaudeInteractiveParser
from .codex import CodexParser
from .cursor import CursorParser
from .gemini import GeminiParser
from .generic import GenericParser
from .nanocoder import NanocoderParser
from .opencode import OpenCodeParser
from .pi import PiParser

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.agents.catalog import AgentCatalog
    from ralph.agents.idle_watchdog import SubagentPidRegistry

__all__ = [
    "_CUSTOM_COMMAND_REGISTRY",
    "_PARSER_REGISTRY",
    "AgentOutputLine",
    "AgentParser",
    "AgyParser",
    "ClaudeInteractiveParser",
    "ClaudeParser",
    "CodexParser",
    "CursorParser",
    "GeminiParser",
    "GenericParser",
    "NanocoderParser",
    "NdjsonParserBase",
    "OpenCodeParser",
    "PiParser",
    "list_parser_keys",
    "resolve_parser_key",
]


class _ParserFactory(Protocol):
    def __call__(
        self,
        *,
        subagent_pid_registry: object | None = None,
        subagent_source_label: str | None = None,
    ) -> AgentParser: ...


@runtime_checkable
class _RegisteredParserFactory(_ParserFactory, Protocol):
    transport: AgentTransport


class _CatalogModule(Protocol):
    def default_catalog(self) -> AgentCatalog: ...


def _catalog_module() -> _CatalogModule:
    return cast("_CatalogModule", importlib.import_module("ralph.agents.catalog"))


def __getattr__(name: str) -> object:
    module_globals: dict[str, object] = cast("dict[str, object]", globals())
    if name == "_PARSER_REGISTRY":
        state_parsers: Mapping[str, _ParserFactory] = cast(
            "Mapping[str, _ParserFactory]",
            _catalog_module().default_catalog()._state.parsers,
        )
        parser_view: Mapping[str, _ParserFactory] = types.MappingProxyType(state_parsers)
        module_globals["_PARSER_REGISTRY"] = parser_view
        return parser_view
    if name == "_CUSTOM_COMMAND_REGISTRY":
        state_commands: Mapping[str, _ParserFactory] = cast(
            "Mapping[str, _ParserFactory]",
            _catalog_module().default_catalog()._state.commands,
        )
        command_view: Mapping[str, _ParserFactory] = types.MappingProxyType(state_commands)
        module_globals["_CUSTOM_COMMAND_REGISTRY"] = command_view
        return command_view
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def _view(name: str) -> Mapping[str, _ParserFactory]:
    return cast("Mapping[str, _ParserFactory]", getattr(sys.modules[__name__], name))


def resolve_parser_key(
    command: str,
    json_parser: JsonParserType,
    transport: AgentTransport | None,
) -> str:
    command_lower = command.lower() if command else ""
    if command_lower == "claude" and transport == AgentTransport.CLAUDE_INTERACTIVE:
        return "claude_interactive"
    custom_entry = _view("_CUSTOM_COMMAND_REGISTRY").get(command_lower)
    if isinstance(custom_entry, _RegisteredParserFactory) and custom_entry.transport == transport:
        return command_lower
    if transport == AgentTransport.CLAUDE_INTERACTIVE:
        return "claude_interactive"
    command_name = command.split(maxsplit=1)[0].lower() if command else ""
    parser_registry = _view("_PARSER_REGISTRY")
    if command_name and command_name in parser_registry and json_parser == JsonParserType.GENERIC:
        return command_name
    if transport is not None and json_parser == JsonParserType.GENERIC:
        transport_key = str(transport)
        if transport_key in parser_registry:
            return transport_key
    return str(json_parser)


def list_parser_keys() -> tuple[str, ...]:
    return tuple(sorted(_view("_PARSER_REGISTRY")))


def get_parser(
    parser_type: str,
    *,
    subagent_pid_registry: SubagentPidRegistry | None = None,
    subagent_source_label: str | None = None,
) -> AgentParser:
    parser_cls = _view("_PARSER_REGISTRY").get(parser_type.lower())
    if parser_cls is not None:
        return parser_cls(
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )
    custom_entry = _view("_CUSTOM_COMMAND_REGISTRY").get(parser_type.lower())
    if isinstance(custom_entry, _RegisteredParserFactory):
        return custom_entry(
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )
    msg = f"Unknown parser type: {parser_type}"
    raise ValueError(msg)

"""Agent output parsers: one per agent transport, plus a generic fallback.

This package converts raw stdout lines from an agent subprocess into structured
``AgentOutputLine`` objects for the invocation engine in ``ralph.agents.invoke``.

Main entry points:

- ``get_parser(parser_type)`` — factory function; maps a parser type name string
  (``'claude'``, ``'claude_interactive'``, ``'codex'``, ``'gemini'``, ``'opencode'``,
  ``'generic'``) to the corresponding parser instance. Raises ``ValueError`` for
  unknown names.
- ``AgentParser`` — the protocol that all parsers implement; defines ``parse``.
- ``AgentOutputLine`` — structured parse result (``type``, ``content``, ``raw``,
  ``metadata``).
- ``ClaudeParser`` — parses Claude stream-JSON NDJSON output.
- ``ClaudeInteractiveParser`` — parses interactive Claude transcript output.
- ``CodexParser`` — parses Codex per-event JSON output.
- ``GeminiParser`` — parses Gemini output.
- ``OpenCodeParser`` — parses OpenCode NDJSON stream output.
- ``GenericParser`` — fallback parser for unknown or plain-text agent output.

Parser selection is driven by ``AgentConfig.json_parser`` (a ``JsonParserType`` enum
value in ``ralph.config.enums``) or, for agents registered via
``register_agent_support()``, by the agent's command name when
``json_parser == JsonParserType.GENERIC``. The runtime calls ``get_parser()`` with the
resolved key and consumes normalized lines through ``parser.parse()``.

To add a parser for a new agent transport, create a module in this package, implement
``AgentParser``, and register the class in both ``_PARSER_REGISTRY`` and ``__all__``.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport, JsonParserType

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import AgentParser
from .claude import ClaudeParser
from .claude_interactive import ClaudeInteractiveParser
from .codex import CodexParser
from .gemini import GeminiParser
from .generic import GenericParser
from .opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.agents._contracts import StrategyFactory

__all__ = [
    "_CUSTOM_COMMAND_REGISTRY",
    "_PARSER_REGISTRY",
    "AgentOutputLine",
    "AgentParser",
    "ClaudeInteractiveParser",
    "ClaudeParser",
    "CodexParser",
    "GeminiParser",
    "GenericParser",
    "NdjsonParserBase",
    "OpenCodeParser",
    "resolve_parser_key",
]


class _ParserRegistryEntry:
    """Parser factory bundled with the strategy factory registered alongside it.

    Instances are callable so they can be stored directly in
    ``_PARSER_REGISTRY`` without changing that dict's public shape.  The
    bundled ``strategy_factory`` lets runtime resolution select the correct
    strategy for a specific agent command, not just its transport.
    """

    __slots__ = ("parser_factory", "strategy_factory", "transport")

    def __init__(
        self,
        parser_factory: Callable[[], AgentParser],
        strategy_factory: StrategyFactory,
        transport: AgentTransport,
    ) -> None:
        self.parser_factory = parser_factory
        self.strategy_factory = strategy_factory
        self.transport = transport

    def __call__(self) -> AgentParser:
        return self.parser_factory()


# DEPRECATED: write-through state populated atomically by AgentCatalog.add().
# New code should use ralph.agents.catalog.default_catalog() or construct an
# AgentCatalog explicitly. The dicts will be removed in a future release.
# Internal mutable storage (write target for AgentCatalog._write_through).
_PARSER_REGISTRY_DATA: dict[str, Callable[[], AgentParser]] = {
    "claude": ClaudeParser,
    "claude_interactive": ClaudeInteractiveParser,
    "codex": CodexParser,
    "gemini": GeminiParser,
    "opencode": OpenCodeParser,
    "generic": GenericParser,
}

# Public read-only view over the internal mutable dict.
_PARSER_REGISTRY: Mapping[str, Callable[[], AgentParser]] = types.MappingProxyType(
    _PARSER_REGISTRY_DATA
)

# Custom agents registered via ``register_agent_support()`` are keyed here by
# their full executable command string.  This keeps custom command names like
# ``claude wrapper`` from colliding with built-in parser keys like ``claude``.
# DEPRECATED: write-through state populated atomically by AgentCatalog.add().
# New code should use ralph.agents.catalog.default_catalog() or construct an
# AgentCatalog explicitly. The dicts will be removed in a future release.
# Internal mutable storage (write target for AgentCatalog._write_through).
_CUSTOM_COMMAND_REGISTRY_DATA: dict[str, _ParserRegistryEntry] = {}

# Public read-only view over the internal mutable dict.
_CUSTOM_COMMAND_REGISTRY: Mapping[str, _ParserRegistryEntry] = types.MappingProxyType(
    _CUSTOM_COMMAND_REGISTRY_DATA
)


def resolve_parser_key(
    command: str,
    json_parser: JsonParserType,
    transport: AgentTransport,
) -> str:
    """Resolve the parser-type key for an agent configuration.

    The resolution order matches the runtime lookup path:

    1. Agents registered via ``register_agent_support()`` are keyed by their
       command name.  When the command name is present in ``_PARSER_REGISTRY``
       as a bundled entry and the registered transport matches ``transport``,
       that command name is the key.  This lets custom interactive agents
       override the built-in ``claude_interactive`` parser.
    2. Built-in interactive Claude still falls back to the
       ``claude_interactive`` parser when no registered entry matched.
    3. When ``json_parser`` is ``JsonParserType.GENERIC`` and a built-in
       parser is registered under the agent's command name, that command name
       is the key.
    4. Otherwise fall back to ``str(json_parser)``.

    Args:
        command: The agent's configured command string (e.g. ``"claude"``).
        json_parser: The parser type token from the agent configuration.
        transport: Optional transport enum; ``CLAUDE_INTERACTIVE`` is special-cased.

    Returns:
        A parser-type key suitable for :func:`get_parser`.
    """
    command_lower = command.lower() if command else ""
    custom_entry = _CUSTOM_COMMAND_REGISTRY.get(command_lower)
    if isinstance(custom_entry, _ParserRegistryEntry) and custom_entry.transport == transport:
        return command_lower
    if transport == AgentTransport.CLAUDE_INTERACTIVE:
        return "claude_interactive"
    command_name = command.split(maxsplit=1)[0].lower() if command else ""
    if command_name and command_name in _PARSER_REGISTRY and json_parser == JsonParserType.GENERIC:
        return command_name
    return str(json_parser)


def get_parser(parser_type: str) -> AgentParser:
    """Get parser instance by type name or by a registered custom command.

    Args:
        parser_type: Parser type name (claude, codex, gemini, opencode, generic)
            or the full executable command of a custom-registered agent.

    Returns:
        Parser instance implementing AgentParser protocol.

    Raises:
        ValueError: If parser type is unknown.
    """
    parser_cls = _PARSER_REGISTRY.get(parser_type.lower())
    if parser_cls is not None:
        return parser_cls()
    custom_entry = _CUSTOM_COMMAND_REGISTRY.get(parser_type.lower())
    if isinstance(custom_entry, _ParserRegistryEntry):
        return custom_entry.parser_factory()
    msg = f"Unknown parser type: {parser_type}"
    raise ValueError(msg)

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

from typing import TYPE_CHECKING, Protocol

from ralph.config.enums import AgentTransport, JsonParserType

from .agent_output_line import AgentOutputLine
from .base import AgentParser
from .claude import ClaudeParser
from .claude_interactive import ClaudeInteractiveParser
from .codex import CodexParser
from .gemini import GeminiParser
from .generic import GenericParser
from .opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.process.child_liveness import ChildLivenessRegistry

__all__ = [
    "_PARSER_REGISTRY",
    "AgentOutputLine",
    "AgentParser",
    "ClaudeInteractiveParser",
    "ClaudeParser",
    "CodexParser",
    "GeminiParser",
    "GenericParser",
    "OpenCodeParser",
    "resolve_parser_key",
]


class _StrategyFactory(Protocol):
    """Factory that returns a ``BaseExecutionStrategy`` with runtime kwargs."""

    def __call__(
        self,
        *,
        label_scope: str | None,
        registry: ChildLivenessRegistry | None,
    ) -> BaseExecutionStrategy: ...


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
        strategy_factory: _StrategyFactory,
        transport: AgentTransport,
    ) -> None:
        self.parser_factory = parser_factory
        self.strategy_factory = strategy_factory
        self.transport = transport

    def __call__(self) -> AgentParser:
        return self.parser_factory()


_PARSER_REGISTRY: dict[str, Callable[[], AgentParser]] = {
    "claude": ClaudeParser,
    "claude_interactive": ClaudeInteractiveParser,
    "codex": CodexParser,
    "gemini": GeminiParser,
    "opencode": OpenCodeParser,
    "generic": GenericParser,
}


def resolve_parser_key(
    command: str,
    json_parser: JsonParserType,
    transport: AgentTransport | None = None,
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
    command_name = command.split(maxsplit=1)[0].lower() if command else ""
    entry = _PARSER_REGISTRY.get(command_name)
    if isinstance(entry, _ParserRegistryEntry) and entry.transport == transport:
        return command_name
    if transport == AgentTransport.CLAUDE_INTERACTIVE:
        return "claude_interactive"
    if (
        command_name
        and command_name in _PARSER_REGISTRY
        and json_parser == JsonParserType.GENERIC
    ):
        return command_name
    return str(json_parser)


def get_parser(parser_type: str) -> AgentParser:
    """Get parser instance by type name.

    Args:
        parser_type: Parser type name (claude, codex, gemini, opencode, generic).

    Returns:
        Parser instance implementing AgentParser protocol.

    Raises:
        ValueError: If parser type is unknown.
    """
    parser_cls = _PARSER_REGISTRY.get(parser_type.lower())
    if parser_cls is None:
        msg = f"Unknown parser type: {parser_type}"
        raise ValueError(msg)
    return parser_cls()

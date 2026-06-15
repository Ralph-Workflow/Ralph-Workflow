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

from typing import TYPE_CHECKING

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
]

_PARSER_REGISTRY: dict[str, Callable[[], AgentParser]] = {
    "claude": ClaudeParser,
    "claude_interactive": ClaudeInteractiveParser,
    "codex": CodexParser,
    "gemini": GeminiParser,
    "opencode": OpenCodeParser,
    "generic": GenericParser,
}


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

"""Agent output parsers: one per agent transport, plus a generic fallback.

This package converts raw stdout lines from an agent subprocess into structured
``AgentOutputLine`` objects for the invocation engine in ``ralph.agents.invoke``.

Main entry points:

- ``get_parser(parser_type)`` — factory function; maps a parser type name string
  (``'claude'``, ``'codex'``, ``'gemini'``, ``'opencode'``, ``'generic'``) to the
  corresponding parser instance. Raises ``ValueError`` for unknown names.
- ``AgentParser`` — the protocol that all parsers implement; defines ``parse_line``.
- ``AgentOutputLine`` — structured parse result (content, kind, raw text).
- ``ClaudeParser`` — parses Claude stream-JSON NDJSON output.
- ``CodexParser`` — parses Codex per-event JSON output.
- ``GeminiParser`` — parses Gemini output.
- ``OpenCodeParser`` — parses OpenCode NDJSON stream output.
- ``GenericParser`` — fallback parser for unknown or plain-text agent output.

Parser selection is driven by ``AgentConfig.json_parser`` (a ``JsonParserType`` enum
value in ``ralph.config.enums``). The invocation engine calls ``get_parser()`` at the
start of each agent run and feeds every stdout line through ``parser.parse_line()``.

To add a parser for a new agent transport, create a module in this package, implement
``AgentParser``, and register the class in both ``get_parser()`` and ``__all__``.
"""

from ralph.agents.parsers.base import AgentOutputLine, AgentParser
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.gemini import GeminiParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.opencode import OpenCodeParser

__all__ = [
    "AgentOutputLine",
    "AgentParser",
    "ClaudeInteractiveParser",
    "ClaudeParser",
    "CodexParser",
    "GeminiParser",
    "GenericParser",
    "OpenCodeParser",
]


def get_parser(parser_type: str) -> AgentParser:
    """Get parser instance by type name.

    Args:
        parser_type: Parser type name (claude, codex, gemini, opencode, generic).

    Returns:
        Parser instance implementing AgentParser protocol.

    Raises:
        ValueError: If parser type is unknown.
    """
    parsers: dict[str, type[AgentParser]] = {
        "claude": ClaudeParser,
        "claude_interactive": ClaudeInteractiveParser,
        "codex": CodexParser,
        "gemini": GeminiParser,
        "opencode": OpenCodeParser,
        "generic": GenericParser,
    }

    parser_cls = parsers.get(parser_type.lower())
    if parser_cls is None:
        msg = f"Unknown parser type: {parser_type}"
        raise ValueError(msg)
    return parser_cls()

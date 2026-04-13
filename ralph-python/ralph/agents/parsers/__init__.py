"""Agent output parsing package."""

from ralph.agents.parsers.base import AgentOutputLine, AgentParser
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.gemini import GeminiParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.opencode import OpenCodeParser

__all__ = [
    "AgentOutputLine",
    "AgentParser",
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

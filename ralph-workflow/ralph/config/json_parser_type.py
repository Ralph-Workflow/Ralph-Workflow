"""JSON parser type enum for agent output parsing."""

from enum import StrEnum


class JsonParserType(StrEnum):
    """JSON parser type for agent output parsing.

    Attributes:
        CLAUDE: Parser for Claude's NDJSON streaming format
        CODEX: Parser for Codex's NDJSON streaming format
        GEMINI: Parser for Gemini's NDJSON streaming format
        OPENCODE: Parser for OpenCode's NDJSON streaming format
        GENERIC: Generic NDJSON parser for other agents
    """

    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    OPENCODE = "opencode"
    GENERIC = "generic"

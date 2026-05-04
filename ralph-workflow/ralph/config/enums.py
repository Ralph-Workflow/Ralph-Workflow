"""Enumeration types for ralph configuration.

NOTE: PipelinePhase is now a type alias to str (not a StrEnum).
Phase names are loaded from pipeline.toml at startup. Well-known phases
are exposed as module-level constants for use in built-in phase handlers.
"""

from __future__ import annotations

from enum import StrEnum


class Verbosity(StrEnum):
    """Verbosity level for Ralph output.

    Attributes:
        QUIET: Minimal output (errors only)
        NORMAL: Default verbosity level
        VERBOSE: More detailed output
        FULL: Full output with all details
        DEBUG: Debug-level output for troubleshooting
    """

    QUIET = "quiet"
    NORMAL = "normal"
    VERBOSE = "verbose"
    FULL = "full"
    DEBUG = "debug"


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


class AgentTransport(StrEnum):
    """Invocation/MCP transport type for an agent runtime.

    Attributes:
        CLAUDE: Claude Code compatible invocation/MCP transport.
        CODEX: Codex CLI compatible invocation/MCP transport.
        OPENCODE: OpenCode compatible invocation/MCP transport.
        GENERIC: No special transport support.
    """

    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    GENERIC = "generic"


class RecoveryStrategy(StrEnum):
    """Recovery strategy when pipeline encounters failures.

    Attributes:
        FAIL: Fail immediately on errors
        AUTO: Attempt automatic recovery
        FORCE: Force through errors
    """

    FAIL = "fail"
    AUTO = "auto"
    FORCE = "force"


class PauseOnExit(StrEnum):
    """Pause behavior before process exit.

    Attributes:
        AUTO: Pause only on standalone failure
        ALWAYS: Always pause before exit
        NEVER: Never pause before exit
    """

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


# ---------------------------------------------------------------------------
# Pipeline phase type alias — phases come from pipeline.toml, not a fixed enum
# ---------------------------------------------------------------------------

PipelinePhase = str

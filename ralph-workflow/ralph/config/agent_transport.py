"""Agent transport enum for runtime invocation/MCP support."""

from enum import StrEnum


class AgentTransport(StrEnum):
    """Invocation/MCP transport type for an agent runtime.

    Attributes:
        CLAUDE: Claude Code compatible invocation/MCP transport.
        CLAUDE_INTERACTIVE: Unattended interactive Claude Code transport.
        CODEX: Codex CLI compatible invocation/MCP transport.
        OPENCODE: OpenCode compatible invocation/MCP transport.
        GENERIC: No special transport support.
        AGY: Google Anti Gravity compatible invocation/MCP transport.
    """

    CLAUDE = "claude"
    CLAUDE_INTERACTIVE = "claude_interactive"
    CODEX = "codex"
    OPENCODE = "opencode"
    GENERIC = "generic"
    AGY = "agy"

"""Agent transport enum for runtime invocation/MCP support."""

from enum import StrEnum


class AgentTransport(StrEnum):
    """Invocation/MCP transport type for an agent runtime.

    Attributes:
        CLAUDE: Claude Code compatible invocation/MCP transport.
        CLAUDE_INTERACTIVE: Unattended interactive Claude Code transport.
        CODEX: Codex CLI compatible invocation/MCP transport.
        OPENCODE: OpenCode compatible invocation/MCP transport.
        NANOCODER: Nanocoder CLI compatible invocation/MCP transport.
        GENERIC: No special transport support.
        AGY: Google Anti Gravity compatible invocation/MCP transport.
        PI: Pi coding agent (pi.dev) compatible invocation/MCP transport. The
            headless BuiltinAgentSpec uses `pi --mode json <prompt>` per
            https://pi.dev/docs/latest/usage. Ralph wires MCP through a
            generated Pi extension and treats clean exits without required
            completion evidence as resumable against the captured Pi session.
        CURSOR: Cursor Agent CLI compatible invocation/MCP transport. The
            headless BuiltinAgentSpec uses ``agent --print --output-format
            stream-json`` and Ralph wires MCP through ``.cursor/mcp.json`` /
            ``~/.cursor/mcp.json`` (the documented Cursor config surface).
    """

    CLAUDE = "claude"
    CLAUDE_INTERACTIVE = "claude_interactive"
    CODEX = "codex"
    OPENCODE = "opencode"
    NANOCODER = "nanocoder"
    GENERIC = "generic"
    AGY = "agy"
    PI = "pi"
    CURSOR = "cursor"

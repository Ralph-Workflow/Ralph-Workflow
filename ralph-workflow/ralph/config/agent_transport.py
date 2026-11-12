from enum import StrEnum


class AgentTransport(StrEnum):
    CLAUDE = "claude"
    CLAUDE_INTERACTIVE = "claude_interactive"
    CODEX = "codex"
    OPENCODE = "opencode"
    NANOCODER = "nanocoder"
    GENERIC = "generic"
    AGY = "agy"
    PI = "pi"
    CURSOR = "cursor"
    KIMI = "kimi"

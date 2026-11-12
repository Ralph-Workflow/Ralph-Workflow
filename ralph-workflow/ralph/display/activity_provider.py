from enum import StrEnum


class ActivityProvider(StrEnum):
    AGY = "agy"
    CLAUDE = "claude"
    CLAUDE_INTERACTIVE = "claude_interactive"
    CODEX = "codex"
    CURSOR = "cursor"
    OPENCODE = "opencode"
    NANOCODER = "nanocoder"
    GEMINI = "gemini"
    GENERIC = "generic"
    PI = "pi"
    UNKNOWN = "unknown"


_TRANSPORT_TO_PROVIDER: dict[str, ActivityProvider] = {
    "claude": ActivityProvider.CLAUDE,
    "claude_interactive": ActivityProvider.CLAUDE_INTERACTIVE,
    "codex": ActivityProvider.CODEX,
    "cursor": ActivityProvider.CURSOR,
    "opencode": ActivityProvider.OPENCODE,
    "nanocoder": ActivityProvider.NANOCODER,
    "gemini": ActivityProvider.GEMINI,
    "agy": ActivityProvider.AGY,
    "pi": ActivityProvider.PI,
    "generic": ActivityProvider.GENERIC,
}


def provider_for_transport(transport: str | None) -> ActivityProvider:
    if transport is None:
        return ActivityProvider.GENERIC
    return _TRANSPORT_TO_PROVIDER.get(transport, ActivityProvider.GENERIC)


__all__ = ["ActivityProvider", "provider_for_transport"]

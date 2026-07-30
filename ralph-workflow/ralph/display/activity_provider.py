"""Canonical provider identities for activity events."""

from enum import StrEnum


class ActivityProvider(StrEnum):
    """Canonical provider identity for agent activity events.

    Each value is the canonical identity used on the activity-event
    bus (the ``AgentActivityEvent.provider`` field). The enum mirrors
    ``AgentTransport`` for the agents where one identity implies the
    other (claude, opencode, codex, gemini, agy, generic). Claude
    Interactive, Nanocoder, and Pi are listed separately because they
    have their own parsers and the prompt's "ALL supported agents"
    requirement means their activity-event stream must also be surfaced
    through the router /on_event path -- not silently collapsed to
    ``GENERIC`` by the CLI-substring detection in
    ``detect_provider_from_command``.
    """

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
    """Return the canonical ``ActivityProvider`` for an ``AgentTransport`` value.

    Falls back to ``ActivityProvider.GENERIC`` when ``transport`` is
    ``None`` or unknown so callers can blindly forward optional
    transport values without worrying about the ActivityProvider enum.
    """
    if transport is None:
        return ActivityProvider.GENERIC
    return _TRANSPORT_TO_PROVIDER.get(transport, ActivityProvider.GENERIC)


__all__ = ["ActivityProvider", "provider_for_transport"]

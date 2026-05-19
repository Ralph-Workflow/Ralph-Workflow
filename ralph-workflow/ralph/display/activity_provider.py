"""Canonical provider identities for activity events."""

from enum import StrEnum


class ActivityProvider(StrEnum):
    """Canonical provider identity for agent activity events."""

    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    GEMINI = "gemini"
    GENERIC = "generic"
    UNKNOWN = "unknown"


__all__ = ["ActivityProvider"]

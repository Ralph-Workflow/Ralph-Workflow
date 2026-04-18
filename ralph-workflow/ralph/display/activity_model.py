"""Typed cross-layer activity contract for parser and display integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ActivityProvider(StrEnum):
    """Canonical provider identity for agent activity events."""

    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    GEMINI = "gemini"
    GENERIC = "generic"
    UNKNOWN = "unknown"


class ActivityEventKind(StrEnum):
    """Canonical event kinds emitted across providers."""

    TEXT = "text"
    STATUS = "status"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    LIFECYCLE = "lifecycle"
    HEARTBEAT = "heartbeat"
    PROGRESS = "progress"
    UNKNOWN = "unknown"


class ActivityVisibilityHint(StrEnum):
    """Visibility intent used by later presenter and display layers."""

    VISIBLE = "visible"
    HIDDEN = "hidden"
    FALLBACK_ONLY = "fallback_only"


@dataclass(frozen=True, slots=True)
class AgentActivityEvent:
    """Typed canonical activity event for future parser normalization work."""

    provider: ActivityProvider
    kind: ActivityEventKind
    content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    visibility: ActivityVisibilityHint = ActivityVisibilityHint.VISIBLE
    source: str = ""
    sequence: int | None = None
    timestamp: str | None = None

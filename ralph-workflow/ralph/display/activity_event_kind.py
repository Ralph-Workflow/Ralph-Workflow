"""Canonical activity event kinds."""

from enum import StrEnum


class ActivityEventKind(StrEnum):
    """Canonical event kinds emitted across providers."""

    TEXT = "text"
    THINKING = "thinking"
    STATUS = "status"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    LIFECYCLE = "lifecycle"
    HEARTBEAT = "heartbeat"
    PROGRESS = "progress"
    UNKNOWN = "unknown"


__all__ = ["ActivityEventKind"]

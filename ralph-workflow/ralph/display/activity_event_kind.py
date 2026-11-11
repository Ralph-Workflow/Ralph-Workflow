
from enum import StrEnum


class ActivityEventKind(StrEnum):

    TEXT = "text"
    THINKING = "thinking"
    STATUS = "status"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    LIFECYCLE = "lifecycle"
    HEARTBEAT = "heartbeat"
    PROGRESS = "progress"
    SUBAGENT_PROGRESS = "subagent_progress"
    UNKNOWN = "unknown"


__all__ = ["ActivityEventKind"]

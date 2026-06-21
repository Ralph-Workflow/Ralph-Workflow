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
    # Live operator-visible subagent progress. Emitted by
    # ``stream_parsed_agent_activity`` whenever a per-parser
    # ``emit_subagent_activity`` hook fires so the operator sees
    # real-time per-tool subagent progress on the console transcript
    # (not only as a ``WaitingStatusEvent`` comma-suffix breadcrumb).
    SUBAGENT_PROGRESS = "subagent_progress"
    UNKNOWN = "unknown"


__all__ = ["ActivityEventKind"]

"""Watchdog-relevant agent activity kind enumeration."""

from enum import StrEnum


class AgentActivityKind(StrEnum):
    """Kinds of agent activity that can reset the idle watchdog."""

    OUTPUT_LINE = "output_line"
    STREAM_DELTA = "stream_delta"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    LIFECYCLE = "lifecycle"
    ERROR_LINE = "error_line"
    PROGRESS_REPORT = "progress_report"
    CHILD_PROCESS = "child_process"
    CHILD_HEARTBEAT = "child_heartbeat"
    CHILD_PROGRESS = "child_progress"
    CHILD_TERMINAL_ACK = "child_terminal_ack"

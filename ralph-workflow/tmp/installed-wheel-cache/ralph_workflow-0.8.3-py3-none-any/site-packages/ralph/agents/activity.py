"""Watchdog-relevant activity signals emitted by agent transports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AgentActivityKind(StrEnum):
    """Kinds of agent activity that can reset the idle watchdog."""

    OUTPUT_LINE = "output_line"
    STREAM_DELTA = "stream_delta"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    LIFECYCLE = "lifecycle"
    CHILD_PROCESS = "child_process"
    CHILD_HEARTBEAT = "child_heartbeat"
    CHILD_PROGRESS = "child_progress"
    CHILD_TERMINAL_ACK = "child_terminal_ack"


@dataclass(frozen=True, slots=True)
class AgentActivitySignal:
    """Small transport-neutral signal consumed by timeout control flow."""

    kind: AgentActivityKind
    raw: str = ""


from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._evidence_tier import (
    ChannelEvidenceSummary,
    ChannelName,
)

if TYPE_CHECKING:
    from ralph.process._alive_by import AliveBy


__all__ = [
    "ChannelEvidenceSummary",
    "ChannelName",
    "CorroborationSnapshot",
    "WaitingCorroborator",
]


@dataclass(frozen=True)
class CorroborationSnapshot:

    workspace_event_count: int | None = None
    oldest_child_seconds: float | None = None
    scoped_child_active: bool | None = None
    scoped_child_count: int | None = None
    terminal_child_events_total: int | None = None
    last_activity_was_meaningful: bool | None = None
    alive_by: AliveBy | None = None
    mcp_tool_call_count: int | None = None
    subagent_progress_count: int | None = None
    last_mcp_tool_call_at: float | None = None
    last_subagent_progress_at: float | None = None
    last_workspace_event_at: float | None = None
    current_run_idle_elapsed_seconds: float | None = None


WaitingCorroborator = Callable[[], CorroborationSnapshot]

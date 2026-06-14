"""Corroboration snapshot for idle watchdog."""

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


# Backward-compatible re-exports. The canonical definitions now live in
# ``_evidence_tier.py`` so the tier model is centralised; this module keeps
# the same public surface for existing consumers.
__all__ = [
    "ChannelEvidenceSummary",
    "ChannelName",
    "CorroborationSnapshot",
    "WaitingCorroborator",
]


@dataclass(frozen=True)
class CorroborationSnapshot:
    """Advisory snapshot of corroborating signals for WAITING_ON_CHILD diagnosis.

    All fields are Optional so callers without a given source can leave them None.
    Corroborators are advisory only; they NEVER affect WatchdogVerdict. The hard
    stop is determined solely by max_waiting_on_child_seconds and max_session_seconds.

    Per-channel activity evidence fields (mcp_tool_call_count,
    subagent_progress_count, last_mcp_tool_call_at, last_subagent_progress_at,
    last_workspace_event_at) carry the evidence the watchdog needs to defer a
    NO_OUTPUT_DEADLINE verdict while work is happening on a non-stdout
    channel. They default to None so existing construction sites remain valid;
    a fully-populated snapshot is the canonical "rich" evidence surface that
    IdleWatchdog.last_evidence_summary() reduces for the verdict hook.
    """

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


from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .waiting_status_kind import WaitingStatusKind


@dataclass(frozen=True)
class WaitingStatusEvent:

    kind: WaitingStatusKind
    cumulative_seconds: float
    current_run_seconds: float
    idle_elapsed_seconds: float
    ceiling_seconds: float
    suspect_threshold_seconds: float | None
    diagnostic: dict[str, str | int | float | bool | list[object]] = field(default_factory=dict)
    subagent_activity: str | None = None
    last_subagent_progress_at: float | None = None
    current_subagent_tool_call: str | None = None
    stall_active: bool = field(kw_only=True)


WaitingStatusListener = Callable[[WaitingStatusEvent], None]

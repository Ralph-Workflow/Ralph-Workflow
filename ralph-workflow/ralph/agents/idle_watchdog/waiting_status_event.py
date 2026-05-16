"""Waiting status event for idle watchdog corroboration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .waiting_status_kind import WaitingStatusKind


@dataclass(frozen=True)
class WaitingStatusEvent:
    """Structured status event emitted by IdleWatchdog during WAITING_ON_CHILD deferral.

    This dataclass is frozen so subscribers cannot accidentally mutate shared state.

    The ``diagnostic`` dict is a forward-compatible extension point for Phase 3
    corroborating signals (workspace_event_delta, oldest_child_seconds,
    scoped_child_active, etc.). This plan ships only the throttle, transition,
    suspicion, and hard-stop summary semantics; Phase 3 fields are out of scope.

    Attributes:
        kind: The type of event (ENTERED, PROGRESS, SUSPECTED_FROZEN, EXITED, HARD_STOP).
        cumulative_seconds: Cumulative WAITING_ON_CHILD seconds across the session so far.
        current_run_seconds: Seconds spent in the current WAITING_ON_CHILD run.
        idle_elapsed_seconds: Seconds since last record_activity() call.
        ceiling_seconds: The active WAITING_ON_CHILD ceiling for this event.
        suspect_threshold_seconds: The suspect_waiting_on_child_seconds threshold, or None.
        diagnostic: Optional dict of extra diagnostic keys for HARD_STOP events.
    """

    kind: WaitingStatusKind
    cumulative_seconds: float
    current_run_seconds: float
    idle_elapsed_seconds: float
    ceiling_seconds: float
    suspect_threshold_seconds: float | None
    diagnostic: dict[str, str | int | float | bool] = field(default_factory=dict)


WaitingStatusListener = Callable[[WaitingStatusEvent], None]

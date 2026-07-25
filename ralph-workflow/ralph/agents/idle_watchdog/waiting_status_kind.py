"""Enumeration of waiting status kinds for the idle watchdog."""

from enum import StrEnum


class WaitingStatusKind(StrEnum):
    """Kind of waiting-status event emitted by IdleWatchdog.

    ENTERED: transition into WAITING_ON_CHILD deferral.
    PROGRESS: periodic status update while still waiting (rate-limited).
    SUSPECTED_FROZEN: cumulative wait crossed suspect threshold; child may be frozen.
    EXITED: transition out of WAITING_ON_CHILD (activity or drain resumed).
    HARD_STOP: cumulative ceiling crossed; watchdog about to fire CHILDREN_PERSIST_TOO_LONG.
    SUBAGENT_PROGRESS: per-subagent progress surface for the waiting-status
    stream. Reuses the parser-layer ActivityEventKind.SUBAGENT_PROGRESS
    surface (which already exists at the parser layer for every
    AgentTransport via the cross-transport visibility test) so the
    waiting-status stream surfaces the live subagent's current
    activity. The emit is rate-limited by
    ``TimeoutPolicy.watchdog_subagent_progress_interval_seconds`` so
    the new event does NOT introduce additional churn versus the
    existing PROGRESS cadence (both default to 30 s).
    STALLED: transition into a stall (single source of truth for the
    Status Bar's ``STALLED`` label). Emitted once on entry into a stall
    via the watchdog's own assessment (SUSPECTED_FROZEN, HARD_STOP, or
    a FIRE verdict for a non-absolute reason). Emitted ONLY on the
    initial transition (no per-tick spam); the watchdog's ``_set_stall``
    helper dedupes by the runtime stall-state flag.
    STALL_RESUMED: transition OUT of a stall. Emitted exactly once on
    the OFF transition (record_activity / record_invocation_start /
    EXITED). Mirrors STALLED so downstream consumers can render explicit
    lines for both transition markers without falling through to the
    HARD_STOP template.
    """

    ENTERED = "entered"
    PROGRESS = "progress"
    SUSPECTED_FROZEN = "suspected_frozen"
    EXITED = "exited"
    HARD_STOP = "hard_stop"
    SUBAGENT_PROGRESS = "subagent_progress"
    STALLED = "stalled"
    STALL_RESUMED = "stall_resumed"
